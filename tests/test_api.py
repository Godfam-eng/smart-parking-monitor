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
    state.get_current_status.return_value = None

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
        api_module._vision.check_home_spot.return_value = {
            "status": "OCCUPIED",
            "confidence": "high",
            "description": "A blue car is parked.",
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
        api_module._vision.check_home_spot.return_value = {
            "status": "UNKNOWN",
            "confidence": "low",
            "description": "Cannot determine.",
        }
        resp = await self.client.get("/status")
        assert resp.status == 200
        text = await resp.text()
        assert "Unable" in text or "unable" in text.lower() or "determine" in text.lower()

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
