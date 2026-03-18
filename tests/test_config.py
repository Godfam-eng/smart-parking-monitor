"""
tests/test_config.py — Tests for config.py
"""

import os
import pytest

from config import load_config, validate, _parse_scan_positions, Config


class TestScanPositionParsing:
    def test_parse_comma_separated(self):
        result = _parse_scan_positions("-60,-30,0,30,60")
        assert result == [-60, -30, 0, 30, 60]

    def test_parse_with_spaces(self):
        result = _parse_scan_positions("-60, -30, 0, 30, 60")
        assert result == [-60, -30, 0, 30, 60]

    def test_parse_single_value(self):
        result = _parse_scan_positions("0")
        assert result == [0]

    def test_parse_invalid_returns_defaults(self):
        result = _parse_scan_positions("not,valid,numbers")
        assert result == [-60, -30, 0, 30, 60]

    def test_parse_empty_string_returns_defaults(self):
        result = _parse_scan_positions("")
        assert result == [-60, -30, 0, 30, 60]


class TestConfigDefaults:
    def test_defaults_applied_when_env_missing(self, monkeypatch):
        """Non-sensitive settings should have defaults even when env vars absent."""
        for key in (
            "TAPO_RTSP_PORT",
            "TAPO_STREAM_PATH",
            "CLAUDE_MODEL",
            "CLAUDE_MAX_TOKENS",
            "CHECK_INTERVAL",
            "CONFIDENCE_THRESHOLD",
            "QUIET_HOURS_START",
            "QUIET_HOURS_END",
            "API_HOST",
            "API_PORT",
            "DB_PATH",
        ):
            monkeypatch.delenv(key, raising=False)

        cfg = load_config()
        assert cfg.TAPO_RTSP_PORT == 554
        assert cfg.TAPO_STREAM_PATH == "stream1"
        assert cfg.CLAUDE_MODEL == "claude-sonnet-4-5"
        assert cfg.CLAUDE_MAX_TOKENS == 1024
        assert cfg.CHECK_INTERVAL == 180
        assert cfg.CONFIDENCE_THRESHOLD == "medium"
        assert cfg.QUIET_HOURS_START == 23
        assert cfg.QUIET_HOURS_END == 7
        assert cfg.API_HOST == "0.0.0.0"
        assert cfg.API_PORT == 8080
        assert cfg.DB_PATH == "parking_history.db"

    def test_default_scan_positions(self, monkeypatch):
        monkeypatch.delenv("SCAN_POSITIONS", raising=False)
        cfg = load_config()
        assert cfg.SCAN_POSITIONS == [-60, -30, 0, 30, 60]


class TestConfigLoadsFromEnv:
    def test_tapo_ip_from_env(self, monkeypatch):
        monkeypatch.setenv("TAPO_IP", "192.168.1.55")
        cfg = load_config()
        assert cfg.TAPO_IP == "192.168.1.55"

    def test_anthropic_key_from_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        cfg = load_config()
        assert cfg.ANTHROPIC_API_KEY == "sk-ant-test-key"

    def test_check_interval_from_env(self, monkeypatch):
        monkeypatch.setenv("CHECK_INTERVAL", "300")
        cfg = load_config()
        assert cfg.CHECK_INTERVAL == 300

    def test_scan_positions_from_env(self, monkeypatch):
        monkeypatch.setenv("SCAN_POSITIONS", "-45,0,45")
        cfg = load_config()
        assert cfg.SCAN_POSITIONS == [-45, 0, 45]

    def test_quiet_hours_from_env(self, monkeypatch):
        monkeypatch.setenv("QUIET_HOURS_START", "22")
        monkeypatch.setenv("QUIET_HOURS_END", "8")
        cfg = load_config()
        assert cfg.QUIET_HOURS_START == 22
        assert cfg.QUIET_HOURS_END == 8


class TestValidation:
    def test_validate_fails_with_missing_required(self):
        cfg = Config()  # All fields empty / default
        assert validate(cfg) is False

    def test_validate_passes_with_all_required(self):
        cfg = Config(
            TAPO_IP="192.168.1.1",
            TAPO_USER="admin",
            TAPO_PASSWORD="password",
            ANTHROPIC_API_KEY="sk-ant-key",
            TELEGRAM_BOT_TOKEN="1234:ABC",
            TELEGRAM_CHAT_ID="987654",
        )
        assert validate(cfg) is True

    def test_validate_fails_missing_tapo_ip(self):
        cfg = Config(
            TAPO_USER="admin",
            TAPO_PASSWORD="password",
            ANTHROPIC_API_KEY="sk-ant-key",
            TELEGRAM_BOT_TOKEN="1234:ABC",
            TELEGRAM_CHAT_ID="987654",
        )
        assert validate(cfg) is False

    def test_validate_passes_without_pushover(self):
        """Pushover is optional — validation should still pass."""
        cfg = Config(
            TAPO_IP="192.168.1.1",
            TAPO_USER="admin",
            TAPO_PASSWORD="password",
            ANTHROPIC_API_KEY="sk-ant-key",
            TELEGRAM_BOT_TOKEN="1234:ABC",
            TELEGRAM_CHAT_ID="987654",
            PUSHOVER_USER_KEY="",
            PUSHOVER_API_TOKEN="",
        )
        assert validate(cfg) is True

    def test_validate_skip_bot_no_telegram_required(self):
        """With require_telegram=False, missing Telegram credentials are allowed."""
        cfg = Config(
            TAPO_IP="192.168.1.1",
            TAPO_USER="admin",
            TAPO_PASSWORD="password",
            ANTHROPIC_API_KEY="sk-ant-key",
            # No TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID
        )
        assert validate(cfg, require_telegram=False) is True

    def test_validate_fails_missing_telegram_when_required(self):
        """With require_telegram=True (default), missing Telegram creds fail."""
        cfg = Config(
            TAPO_IP="192.168.1.1",
            TAPO_USER="admin",
            TAPO_PASSWORD="password",
            ANTHROPIC_API_KEY="sk-ant-key",
            # No TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID
        )
        assert validate(cfg) is False

    def test_validate_passes_without_anthropic_when_not_required(self):
        """require_anthropic=False allows missing Anthropic key."""
        cfg = Config(
            TAPO_IP="192.168.1.1",
            TAPO_USER="admin",
            TAPO_PASSWORD="password",
            # No ANTHROPIC_API_KEY
        )
        assert validate(cfg, require_telegram=False, require_anthropic=False) is True

    def test_validate_fails_without_anthropic_when_required(self):
        """require_anthropic=True (default) requires Anthropic key."""
        cfg = Config(
            TAPO_IP="192.168.1.1",
            TAPO_USER="admin",
            TAPO_PASSWORD="password",
            # No ANTHROPIC_API_KEY
        )
        assert validate(cfg, require_telegram=False, require_anthropic=True) is False


class TestCalibrationConfig:
    def test_calibration_defaults(self, monkeypatch):
        for key in ("AUTO_CALIBRATE", "CALIBRATION_INTERVAL_DAYS", "CALIBRATION_MIN_USEFULNESS", "CALIBRATION_ANGLES"):
            monkeypatch.delenv(key, raising=False)
        cfg = load_config()
        assert cfg.AUTO_CALIBRATE is True
        assert cfg.CALIBRATION_INTERVAL_DAYS == 30
        assert cfg.CALIBRATION_MIN_USEFULNESS == 6
        assert cfg.CALIBRATION_ANGLES == [-90, -75, -60, -45, -30, -15, 0, 15, 30, 45, 60, 75, 90]

    def test_auto_calibrate_from_env(self, monkeypatch):
        monkeypatch.setenv("AUTO_CALIBRATE", "false")
        cfg = load_config()
        assert cfg.AUTO_CALIBRATE is False

    def test_calibration_interval_from_env(self, monkeypatch):
        monkeypatch.setenv("CALIBRATION_INTERVAL_DAYS", "14")
        cfg = load_config()
        assert cfg.CALIBRATION_INTERVAL_DAYS == 14

    def test_calibration_min_usefulness_from_env(self, monkeypatch):
        monkeypatch.setenv("CALIBRATION_MIN_USEFULNESS", "7")
        cfg = load_config()
        assert cfg.CALIBRATION_MIN_USEFULNESS == 7

    def test_calibration_angles_from_env(self, monkeypatch):
        monkeypatch.setenv("CALIBRATION_ANGLES", "-45,0,45")
        cfg = load_config()
        assert cfg.CALIBRATION_ANGLES == [-45, 0, 45]
