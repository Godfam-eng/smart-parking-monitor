"""
tests/test_notifications.py — Tests for notifications.py
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from config import Config
from notifications import NotificationManager


@pytest.fixture
def cfg():
    return Config(
        PUSHOVER_USER_KEY="user-key",
        PUSHOVER_API_TOKEN="api-token",
        TELEGRAM_BOT_TOKEN="1234:TOKEN",
        TELEGRAM_CHAT_ID="999888",
        QUIET_HOURS_START=23,
        QUIET_HOURS_END=7,
    )


@pytest.fixture
def nm(cfg):
    return NotificationManager(cfg)


# ------------------------------------------------------------------
# Quiet hours tests
# ------------------------------------------------------------------

class TestIsQuietHours:
    @pytest.mark.parametrize("hour,expected", [
        (23, True),   # start of quiet period
        (0, True),    # midnight
        (3, True),    # middle of night
        (6, True),    # just before end
        (7, False),   # quiet period ends at 7
        (12, False),  # noon
        (22, False),  # one hour before quiet starts
    ])
    def test_midnight_crossing(self, nm, hour, expected, monkeypatch):
        """Quiet hours 23–07 span midnight."""
        mock_dt = MagicMock()
        mock_dt.now.return_value = MagicMock(hour=hour)
        monkeypatch.setattr("notifications.datetime", mock_dt)
        assert nm.is_quiet_hours() == expected

    def test_simple_range_inside(self, cfg, monkeypatch):
        """Quiet hours 09–17 (no midnight crossing)."""
        cfg.QUIET_HOURS_START = 9
        cfg.QUIET_HOURS_END = 17
        nm = NotificationManager(cfg)
        mock_dt = MagicMock()
        mock_dt.now.return_value = MagicMock(hour=12)
        monkeypatch.setattr("notifications.datetime", mock_dt)
        assert nm.is_quiet_hours() is True

    def test_simple_range_outside(self, cfg, monkeypatch):
        """Quiet hours 09–17, hour=20 should be False."""
        cfg.QUIET_HOURS_START = 9
        cfg.QUIET_HOURS_END = 17
        nm = NotificationManager(cfg)
        mock_dt = MagicMock()
        mock_dt.now.return_value = MagicMock(hour=20)
        monkeypatch.setattr("notifications.datetime", mock_dt)
        assert nm.is_quiet_hours() is False


# ------------------------------------------------------------------
# Pushover tests
# ------------------------------------------------------------------

class TestSendPushover:
    def test_sends_when_configured(self, nm):
        with patch("notifications.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            result = nm.send_pushover("Test Title", "Test message")
        assert result is True
        mock_post.assert_called_once()

    def test_returns_false_when_not_configured(self, cfg):
        cfg.PUSHOVER_USER_KEY = ""
        cfg.PUSHOVER_API_TOKEN = ""
        nm = NotificationManager(cfg)
        result = nm.send_pushover("Title", "Message")
        assert result is False

    def test_returns_false_on_http_error(self, nm):
        with patch("notifications.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=400, text="Bad Request")
            result = nm.send_pushover("Title", "Message")
        assert result is False

    def test_returns_false_on_exception(self, nm):
        import requests as req
        with patch("notifications.requests.post", side_effect=req.RequestException("timeout")):
            result = nm.send_pushover("Title", "Message")
        assert result is False

    def test_sends_with_image(self, nm):
        with patch("notifications.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            result = nm.send_pushover("Title", "Message", image=b"fake-jpeg")
        assert result is True
        # Should use files= kwarg when image is provided
        call_kwargs = mock_post.call_args
        assert "files" in call_kwargs.kwargs


# ------------------------------------------------------------------
# Telegram tests
# ------------------------------------------------------------------

class TestSendTelegram:
    def test_sends_text_when_configured(self, nm):
        with patch("notifications.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            result = nm.send_telegram("Hello!")
        assert result is True

    def test_returns_false_when_not_configured(self, cfg):
        cfg.TELEGRAM_BOT_TOKEN = ""
        nm = NotificationManager(cfg)
        result = nm.send_telegram("Hello!")
        assert result is False

    def test_sends_photo_when_image_provided(self, nm):
        with patch("notifications.requests.post") as mock_post:
            mock_post.return_value = MagicMock(status_code=200)
            result = nm.send_telegram("Caption", image=b"fake-jpeg")
        assert result is True
        url_called = mock_post.call_args.args[0]
        assert "sendPhoto" in url_called


# ------------------------------------------------------------------
# High-level notify methods
# ------------------------------------------------------------------

class TestNotifyMethods:
    def test_notify_space_free_uses_both_channels_outside_quiet(self, nm, monkeypatch):
        monkeypatch.setattr(nm, "is_quiet_hours", lambda: False)
        with patch.object(nm, "send_pushover", return_value=True) as mock_push, \
             patch.object(nm, "send_telegram", return_value=True) as mock_tg:
            nm.notify_space_free("No cars visible.")
        mock_push.assert_called_once()
        mock_tg.assert_called_once()

    def test_notify_space_free_skips_pushover_in_quiet_hours(self, nm, monkeypatch):
        monkeypatch.setattr(nm, "is_quiet_hours", lambda: True)
        with patch.object(nm, "send_pushover", return_value=True) as mock_push, \
             patch.object(nm, "send_telegram", return_value=True) as mock_tg:
            nm.notify_space_free("No cars visible.")
        mock_push.assert_not_called()
        mock_tg.assert_called_once()

    def test_notify_space_occupied_only_telegram(self, nm):
        with patch.object(nm, "send_pushover", return_value=True) as mock_push, \
             patch.object(nm, "send_telegram", return_value=True) as mock_tg:
            nm.notify_space_occupied("Car arrived.")
        mock_push.assert_not_called()
        mock_tg.assert_called_once()

    def test_notify_error_message_format(self, nm):
        with patch.object(nm, "send_telegram") as mock_tg:
            nm.notify_error("Something broke")
        args = mock_tg.call_args.kwargs or {}
        message = mock_tg.call_args.args[0] if mock_tg.call_args.args else args.get("message", "")
        assert "Something broke" in message

    def test_notify_startup_sends_telegram(self, nm):
        with patch.object(nm, "send_telegram", return_value=True) as mock_tg:
            nm.notify_startup()
        mock_tg.assert_called_once()

    def test_notify_never_raises(self, nm):
        """All notify methods should silently handle exceptions."""
        with patch.object(nm, "send_pushover", side_effect=Exception("boom")), \
             patch.object(nm, "send_telegram", side_effect=Exception("boom")):
            # Should not raise
            nm.notify_space_free("Test")
            nm.notify_space_occupied("Test")
            nm.notify_error("Test")
            nm.notify_startup()
