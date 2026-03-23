"""
tests/test_notification_batching.py — Tests for notification batching / debounce logic.

Tests that transient state flips are suppressed by the monitoring loop's
confirmation logic, and that confirmed changes trigger notifications.
"""

import sys
import threading
import time
from unittest.mock import MagicMock, call, patch

# Mock hardware dependencies
sys.modules.setdefault("cv2", MagicMock())
sys.modules.setdefault("pytapo", MagicMock())

import pytest
from config import Config
from state import ParkingState


# ---------------------------------------------------------------------------
# Helper to build a minimal Config for notification batching tests
# ---------------------------------------------------------------------------

def _make_config(confirm_seconds: int = 60) -> Config:
    return Config(
        TAPO_IP="192.168.1.1",
        TAPO_USER="admin",
        TAPO_PASSWORD="pass",
        ANTHROPIC_API_KEY="sk-ant-key",
        TELEGRAM_BOT_TOKEN="1234:TOKEN",
        TELEGRAM_CHAT_ID="999",
        CHECK_INTERVAL=10,
        CONFIDENCE_THRESHOLD="low",
        MOTION_GATE_ENABLED=False,
        NOTIFICATION_CONFIRM_SECONDS=confirm_seconds,
        BACKGROUND_SCAN_EVERY=0,
        QUIET_HOURS_START=23,
        QUIET_HOURS_END=7,
        NIGHT_MODE_MODEL="claude-sonnet-4-5",
        NIGHT_MODE_INTERVAL_MULTIPLIER=2,
        SNAPSHOT_HISTORY_ENABLE=False,
        HOMEKIT_ENABLE=False,
    )


# ---------------------------------------------------------------------------
# Unit tests for the pending notification state machine
# ---------------------------------------------------------------------------

class TestNotificationBatchingLogic:
    """
    Tests for the pending-notification / confirmation pattern used in main.py.

    We directly exercise the key decision logic rather than running the full
    monitoring loop (which requires hardware).
    """

    def test_state_records_transient_flip(self):
        """record_transient_flip stores a row in the DB without raising."""
        state = ParkingState(":memory:")
        state.record_transient_flip("FREE", "OCCUPIED", "FREE", "brief van")
        # Verify row exists (no crash = pass, but also verify via query)
        with state._lock:
            row = state._conn.execute(
                "SELECT * FROM transient_flips ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row is not None
        assert row["from_status"] == "FREE"
        assert row["to_status"] == "OCCUPIED"
        assert row["back_status"] == "FREE"
        state.close()

    def test_record_transient_flip_description(self):
        state = ParkingState(":memory:")
        state.record_transient_flip("OCCUPIED", "FREE", "OCCUPIED", "noise")
        with state._lock:
            row = state._conn.execute(
                "SELECT description FROM transient_flips ORDER BY id DESC LIMIT 1"
            ).fetchone()
        assert row["description"] == "noise"
        state.close()

    def test_multiple_flips_recorded(self):
        state = ParkingState(":memory:")
        for i in range(5):
            state.record_transient_flip("FREE", "OCCUPIED", "FREE", f"flip {i}")
        with state._lock:
            count = state._conn.execute(
                "SELECT COUNT(*) FROM transient_flips"
            ).fetchone()[0]
        assert count == 5
        state.close()


class TestPendingNotificationMachine:
    """
    Test the pending-notification state machine logic extracted from main.py
    using a simulation of the relevant decision branches.
    """

    def _run_cycle(
        self,
        previous: str,
        current: str,
        pending: dict,
        confirm_seconds: int,
    ):
        """
        Simulate one monitoring loop iteration's notification logic.

        Returns (new_pending, notification_sent, transient_recorded).
        """
        from notifications import NotificationManager
        from config import Config

        cfg = _make_config(confirm_seconds)
        state = ParkingState(":memory:")
        notif = MagicMock()

        new_pending = pending
        notification_sent = None
        transient_recorded = False

        confidence = "high"

        _CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
        def meets(conf, thresh):
            return _CONFIDENCE_ORDER.get(conf, 0) >= _CONFIDENCE_ORDER.get(thresh, 1)

        if current != "UNKNOWN" and meets(confidence, cfg.CONFIDENCE_THRESHOLD):
            if pending is not None:
                if current == pending["status"]:
                    # Confirmed
                    notification_sent = pending["status"]
                    new_pending = None
                else:
                    # Transient flip
                    state.record_transient_flip(
                        pending["previous"], pending["status"], current, ""
                    )
                    transient_recorded = True
                    new_pending = None
            elif previous is not None and previous != current and confirm_seconds > 0:
                new_pending = {
                    "status": current,
                    "previous": previous,
                    "description": "",
                    "image_bytes": b"",
                    "before_image": None,
                }
            elif previous is not None and previous != current and confirm_seconds == 0:
                notification_sent = current
                new_pending = None

        state.close()
        return new_pending, notification_sent, transient_recorded

    def test_state_change_queues_pending_when_confirm_enabled(self):
        pending, sent, flip = self._run_cycle("FREE", "OCCUPIED", None, 60)
        assert pending is not None
        assert pending["status"] == "OCCUPIED"
        assert sent is None
        assert not flip

    def test_confirmed_change_sends_notification(self):
        existing_pending = {
            "status": "OCCUPIED", "previous": "FREE",
            "description": "", "image_bytes": b"", "before_image": None,
        }
        pending, sent, flip = self._run_cycle("OCCUPIED", "OCCUPIED", existing_pending, 60)
        assert pending is None
        assert sent == "OCCUPIED"
        assert not flip

    def test_transient_flip_is_recorded_not_sent(self):
        existing_pending = {
            "status": "OCCUPIED", "previous": "FREE",
            "description": "", "image_bytes": b"", "before_image": None,
        }
        # Status flipped back to FREE
        pending, sent, flip = self._run_cycle("OCCUPIED", "FREE", existing_pending, 60)
        assert pending is None
        assert sent is None
        assert flip

    def test_immediate_notify_when_confirm_disabled(self):
        pending, sent, flip = self._run_cycle("FREE", "OCCUPIED", None, 0)
        assert pending is None
        assert sent == "OCCUPIED"
        assert not flip

    def test_no_action_on_unknown_status(self):
        pending, sent, flip = self._run_cycle("FREE", "UNKNOWN", None, 60)
        assert pending is None
        assert sent is None
        assert not flip

    def test_no_notification_on_first_run(self):
        # previous is None → first run, no state change notification
        pending, sent, flip = self._run_cycle(None, "FREE", None, 60)
        assert pending is None
        assert sent is None

    def test_no_notification_when_status_unchanged(self):
        pending, sent, flip = self._run_cycle("FREE", "FREE", None, 60)
        assert pending is None
        assert sent is None
