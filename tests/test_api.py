"""
tests/test_api.py — Tests for api.py HTTP endpoints
"""

import json
import sys
from unittest.mock import MagicMock, patch, AsyncMock

# Mock hardware dependencies not available in test environment
sys.modules.setdefault("cv2", MagicMock())
sys.modules.setdefault("pytapo", MagicMock())

import pytest
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from aiohttp import web

import api as api_module
from config import Config


def _make_mocks():
    cfg = Config(
        TAPO_IP="192.168.1.1",
        TAPO_USER="admin",
        TAPO_PASSWORD="pass",
        ANTHROPIC_API_KEY="sk-ant-key",
        TELEGRAM_BOT_TOKEN="1234:TOKEN",
        TELEGRAM_CHAT_ID="999",
        API_HOST="127.0.0.1",
        API_PORT=18080,
    )

    camera = MagicMock()
    camera.grab_frame.return_value = b"\xff\xd8\xff" + b"\x00" * 100  # minimal JPEG
    camera.get_snapshot.return_value = b"\xff\xd8\xff" + b"\x00" * 100
    camera.scan_street.return_value = [
        {"angle": 0, "image": b"\xff\xd8\xff", "position_name": "center"},
    ]
    camera.scan_street_iter.return_value = iter([
        {"angle": 0, "image": b"\xff\xd8\xff", "position_name": "center"},
    ])

    vision = MagicMock()
    vision.check_home_spot.return_value = {
        "status": "FREE",
        "confidence": "high",
        "description": "No cars visible.",
    }
    vision.check_scan_position.return_value = {
        "status": "FREE",
        "confidence": "high",
        "description": "Empty space at center.",
    }

    state = MagicMock()
    state.get_stats.return_value = {
        "total_checks": 10,
        "free_percentage": 70.0,
        "occupied_percentage": 30.0,
        "busiest_hours": [],
        "freest_hours": [],
        "checks_last_24h": 5,
        "state_changes_last_24h": 2,
        "last_check": {"timestamp": "2026-01-01 12:00:00", "status": "FREE"},
        "days_of_data": 7,
    }
    state.get_current_status.return_value = {
        "status": "FREE",
        "confidence": "high",
        "description": "No cars visible.",
        "timestamp": "2026-01-01 12:00:00",
    }
    state.get_watch_mode.return_value = None
    state.get_scan_cache.return_value = None

    return cfg, camera, vision, state


class TestApiEndpoints(AioHTTPTestCase):
    async def get_application(self):
        cfg, camera, vision, state = _make_mocks()
        # Inject into module globals
        api_module._config = cfg
        api_module._camera = camera
        api_module._vision = vision
        api_module._state = state
        api_module._start_time = 0.0
        return api_module._build_app()

    @unittest_run_loop
    async def test_root_returns_ok(self):
        resp = await self.client.get("/")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
        assert data["service"] == "parking-monitor"
        assert data["version"] == "1.0.0"

    @unittest_run_loop
    async def test_status_text_free(self):
        resp = await self.client.get("/status")
        assert resp.status == 200
        text = await resp.text()
        assert "free" in text.lower()

    @unittest_run_loop
    async def test_status_json_structure(self):
        resp = await self.client.get("/status/json")
        assert resp.status == 200
        data = await resp.json()
        assert "status" in data
        assert "confidence" in data
        assert "description" in data
        assert "timestamp" in data

    @unittest_run_loop
    async def test_status_text_occupied(self):
        api_module._state.get_current_status.return_value = {
            "status": "OCCUPIED",
            "confidence": "high",
            "description": "A blue car is parked.",
            "timestamp": "2026-01-01 12:00:00",
        }
        resp = await self.client.get("/status")
        assert resp.status == 200
        text = await resp.text()
        assert "occupied" in text.lower()

    @unittest_run_loop
    async def test_snapshot_returns_jpeg(self):
        resp = await self.client.get("/snapshot")
        assert resp.status == 200
        assert resp.content_type == "image/jpeg"
        body = await resp.read()
        assert body[:3] == b"\xff\xd8\xff"

    @unittest_run_loop
    async def test_stats_endpoint(self):
        resp = await self.client.get("/stats")
        assert resp.status == 200
        data = await resp.json()
        assert "total_checks" in data

    @unittest_run_loop
    async def test_health_endpoint(self):
        resp = await self.client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert "camera" in data
        assert "database" in data
        assert "uptime_seconds" in data

    @unittest_run_loop
    async def test_scan_text(self):
        resp = await self.client.get("/scan")
        assert resp.status == 200
        text = await resp.text()
        assert len(text) > 0

    @unittest_run_loop
    async def test_scan_json(self):
        resp = await self.client.get("/scan/json")
        assert resp.status == 200
        data = await resp.json()
        assert "positions" in data
        assert "timestamp" in data

    @unittest_run_loop
    async def test_status_text_unknown(self):
        api_module._state.get_current_status.return_value = {
            "status": "UNKNOWN",
            "confidence": "low",
            "description": "Cannot determine.",
            "timestamp": "2026-01-01 12:00:00",
        }
        resp = await self.client.get("/status")
        assert resp.status == 200
        text = await resp.text()
        assert "unclear" in text.lower() or "unknown" in text.lower()

    @unittest_run_loop
    async def test_scan_text_no_free_spaces(self):
        api_module._camera.scan_street.return_value = [
            {"angle": 0, "image": b"\xff\xd8\xff", "position_name": "center"},
        ]
        api_module._vision.check_scan_position.return_value = {
            "status": "OCCUPIED",
            "confidence": "high",
            "description": "All spaces taken.",
        }
        resp = await self.client.get("/scan")
        assert resp.status == 200
        text = await resp.text()
        assert "No free" in text or "no free" in text.lower()

    @unittest_run_loop
    async def test_status_text_no_data(self):
        """When no cached data exists, /status returns a startup message."""
        api_module._state.get_current_status.return_value = None
        resp = await self.client.get("/status")
        assert resp.status == 200
        text = await resp.text()
        assert "starting up" in text.lower() or "no parking" in text.lower()

    @unittest_run_loop
    async def test_status_json_no_data(self):
        """When no cached data exists, /status/json returns 503."""
        api_module._state.get_current_status.return_value = None
        resp = await self.client.get("/status/json")
        assert resp.status == 503

    @unittest_run_loop
    async def test_status_live_calls_claude(self):
        """/status/live should do a fresh Claude call and return free/occupied text."""
        api_module._state.get_current_status.return_value = None  # no cached state
        api_module._vision.check_home_spot.return_value = {
            "status": "FREE",
            "confidence": "high",
            "description": "No cars visible.",
        }
        resp = await self.client.get("/status/live")
        assert resp.status == 200
        text = await resp.text()
        assert "free" in text.lower()


class TestAuthMiddleware(AioHTTPTestCase):
    async def get_application(self):
        cfg, camera, vision, state = _make_mocks()
        cfg.API_KEY = "test-secret-key"
        api_module._config = cfg
        api_module._camera = camera
        api_module._vision = vision
        api_module._state = state
        api_module._start_time = 0.0
        return api_module._build_app()

    @unittest_run_loop
    async def test_auth_required_without_key(self):
        resp = await self.client.get("/status")
        assert resp.status == 401

    @unittest_run_loop
    async def test_auth_accepted_with_header(self):
        resp = await self.client.get("/status", headers={"X-API-Key": "test-secret-key"})
        assert resp.status == 200

    @unittest_run_loop
    async def test_auth_accepted_with_query_param(self):
        resp = await self.client.get("/status?key=test-secret-key")
        assert resp.status == 200

    @unittest_run_loop
    async def test_auth_rejected_wrong_query_param(self):
        resp = await self.client.get("/status?key=wrong-key")
        assert resp.status == 401

    @unittest_run_loop
    async def test_health_exempt_from_auth(self):
        """GET /health should not require authentication."""
        resp = await self.client.get("/health")
        assert resp.status == 200

    @unittest_run_loop
    async def test_root_exempt_from_auth(self):
        """GET / should not require authentication."""
        resp = await self.client.get("/")
        assert resp.status == 200


class TestScanVoice(AioHTTPTestCase):
    async def get_application(self):
        cfg, camera, vision, state = _make_mocks()
        api_module._config = cfg
        api_module._camera = camera
        api_module._vision = vision
        api_module._state = state
        api_module._start_time = 0.0
        return api_module._build_app()

    @unittest_run_loop
    async def test_scan_voice_short_circuit_when_home_free(self):
        """When home spot is free with high confidence, /scan/voice short-circuits."""
        api_module._vision.check_home_spot.return_value = {
            "status": "FREE",
            "confidence": "high",
            "description": "No cars.",
        }
        resp = await self.client.get("/scan/voice")
        assert resp.status == 200
        text = await resp.text()
        assert "good news" in text.lower()
        assert "head straight home" in text.lower()

    @unittest_run_loop
    async def test_scan_voice_does_full_scan_when_occupied(self):
        """When home spot is occupied, /scan/voice runs a full scan."""
        api_module._vision.check_home_spot.return_value = {
            "status": "OCCUPIED",
            "confidence": "high",
            "description": "Car parked.",
        }
        api_module._camera.scan_street.return_value = [
            {"angle": -30, "image": b"\xff\xd8\xff", "position_name": "left"},
        ]
        api_module._vision.check_scan_position.return_value = {
            "status": "FREE",
            "confidence": "high",
            "description": "Empty space.",
        }
        resp = await self.client.get("/scan/voice")
        assert resp.status == 200
        text = await resp.text()
        assert "checking your street" in text.lower()

    @unittest_run_loop
    async def test_scan_voice_returns_plain_text(self):
        resp = await self.client.get("/scan/voice")
        assert resp.status == 200
        assert "text/plain" in resp.content_type

    @unittest_run_loop
    async def test_scan_voice_no_short_circuit_on_low_confidence(self):
        """Low confidence FREE should not short-circuit — run full scan."""
        api_module._vision.check_home_spot.return_value = {
            "status": "FREE",
            "confidence": "low",
            "description": "Maybe free.",
        }
        api_module._camera.scan_street.return_value = [
            {"angle": 0, "image": b"\xff\xd8\xff", "position_name": "center"},
        ]
        api_module._vision.check_scan_position.return_value = {
            "status": "FREE",
            "confidence": "low",
            "description": "Looks empty.",
        }
        resp = await self.client.get("/scan/voice")
        assert resp.status == 200
        text = await resp.text()
        # Should have run full scan, not short-circuited
        assert "checking your street" in text.lower()


class TestHealthWatchMode(AioHTTPTestCase):
    async def get_application(self):
        cfg, camera, vision, state = _make_mocks()
        state.get_watch_mode.return_value = None
        api_module._config = cfg
        api_module._camera = camera
        api_module._vision = vision
        api_module._state = state
        api_module._start_time = 0.0
        return api_module._build_app()

    @unittest_run_loop
    async def test_health_includes_watch_mode(self):
        resp = await self.client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert "watch_mode" in data
        assert data["watch_mode"]["active"] is False
        assert data["watch_mode"]["mode"] is None

    @unittest_run_loop
    async def test_health_reports_active_watch_mode(self):
        api_module._state.get_watch_mode.return_value = {
            "mode": "watch",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "active": 1,
        }
        resp = await self.client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["watch_mode"]["active"] is True
        assert data["watch_mode"]["mode"] == "watch"


class TestVoiceNarrative:
    """Unit tests for the _build_voice_narrative helper function."""

    def test_narrative_starts_with_checking(self):
        home = {"status": "OCCUPIED", "confidence": "high", "description": ""}
        narrative = api_module._build_voice_narrative(home, [])
        assert narrative.startswith("Checking your street now.")

    def test_narrative_home_free_short_message(self):
        home = {"status": "FREE", "confidence": "high", "description": ""}
        scan = []
        narrative = api_module._build_voice_narrative(home, scan)
        assert "free" in narrative.lower()
        assert "head straight home" in narrative.lower()

    def test_narrative_occupied_with_free_space_found(self):
        home = {"status": "OCCUPIED", "confidence": "high", "description": ""}
        scan = [
            {"position_name": "left", "status": "FREE", "confidence": "high", "description": ""},
        ]
        narrative = api_module._build_voice_narrative(home, scan)
        assert "i'd head there" in narrative.lower()

    def test_narrative_fully_occupied(self):
        home = {"status": "OCCUPIED", "confidence": "high", "description": ""}
        scan = [
            {"position_name": "left", "status": "OCCUPIED", "confidence": "high", "description": ""},
            {"position_name": "right", "status": "OCCUPIED", "confidence": "high", "description": ""},
        ]
        narrative = api_module._build_voice_narrative(home, scan)
        assert "full" in narrative.lower() or "try again" in narrative.lower()

    def test_narrative_unknown_status_phrase(self):
        home = {"status": "UNKNOWN", "confidence": "low", "description": ""}
        scan = []
        narrative = api_module._build_voice_narrative(home, scan)
        assert "can't quite tell" in narrative.lower()
