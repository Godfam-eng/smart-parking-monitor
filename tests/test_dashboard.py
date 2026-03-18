"""
tests/test_dashboard.py — Tests for the PWA dashboard endpoints
"""

import json
import os
import sys
from unittest.mock import MagicMock

# Mock hardware dependencies not available in test environment
sys.modules.setdefault("cv2", MagicMock())
sys.modules.setdefault("pytapo", MagicMock())

import pytest
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
from aiohttp import web

import api as api_module
from config import Config


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

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
        CHECK_INTERVAL=120,
        QUIET_HOURS_START=23,
        QUIET_HOURS_END=7,
        PARKING_ZONE_TOP=30,
        PARKING_ZONE_BOTTOM=80,
        PARKING_ZONE_LEFT=20,
        PARKING_ZONE_RIGHT=80,
        SCAN_POSITIONS=[-30, 0, 30],
        HOME_POSITION=0,
        CONFIDENCE_THRESHOLD="medium",
        STREET_PARKING_SIDE="near",
        OPPOSITE_SIDE_RESTRICTION="double_yellow",
    )

    camera = MagicMock()
    camera.grab_frame.return_value = b"\xff\xd8\xff" + b"\x00" * 100

    vision = MagicMock()

    state = MagicMock()
    state.get_stats.return_value = {
        "total_checks": 100,
        "free_percentage": 65.0,
        "occupied_percentage": 35.0,
        "busiest_hours": [{"hour": 9, "count": 20}],
        "freest_hours": [{"hour": 3, "count": 15}],
        "checks_last_24h": 48,
        "state_changes_last_24h": 4,
        "last_check": {"timestamp": "2026-03-18 12:00:00", "status": "FREE"},
        "days_of_data": 3,
    }
    state.get_current_status.return_value = {
        "status": "FREE",
        "confidence": "high",
        "description": "No cars visible.",
        "timestamp": "2026-03-18 12:00:00",
    }
    state.get_hourly_breakdown.return_value = [
        {
            "hour": h,
            "total": 10,
            "free": 7,
            "occupied": 3,
            "free_percentage": 70.0,
        }
        for h in range(24)
    ]

    return cfg, camera, vision, state


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestDashboardEndpoints(AioHTTPTestCase):
    async def get_application(self):
        cfg, camera, vision, state = _make_mocks()
        api_module._config = cfg
        api_module._camera = camera
        api_module._vision = vision
        api_module._state = state
        api_module._start_time = 0.0
        api_module._calibrator = None
        return api_module._build_app()

    # ── /dashboard ──────────────────────────────────────────────────

    @unittest_run_loop
    async def test_dashboard_returns_200(self):
        resp = await self.client.get("/dashboard")
        assert resp.status == 200

    @unittest_run_loop
    async def test_dashboard_content_type_html(self):
        resp = await self.client.get("/dashboard")
        assert "text/html" in resp.content_type

    @unittest_run_loop
    async def test_dashboard_contains_tab_bar(self):
        resp = await self.client.get("/dashboard")
        body = await resp.text()
        assert "tab-bar" in body or "tab-btn" in body

    @unittest_run_loop
    async def test_dashboard_contains_screens(self):
        resp = await self.client.get("/dashboard")
        body = await resp.text()
        assert "screen-home" in body
        assert "screen-heatmap" in body or "screen-analytics" in body or "heatmap" in body
        assert "screen-scan" in body
        assert "screen-camera" in body
        assert "screen-settings" in body

    @unittest_run_loop
    async def test_dashboard_contains_pwa_meta_tags(self):
        resp = await self.client.get("/dashboard")
        body = await resp.text()
        assert "apple-mobile-web-app-capable" in body
        assert "manifest" in body

    @unittest_run_loop
    async def test_dashboard_contains_service_worker_registration(self):
        resp = await self.client.get("/dashboard")
        body = await resp.text()
        assert "serviceWorker" in body
        assert "/sw.js" in body

    # ── /manifest.json ──────────────────────────────────────────────

    @unittest_run_loop
    async def test_manifest_returns_200(self):
        resp = await self.client.get("/manifest.json")
        assert resp.status == 200

    @unittest_run_loop
    async def test_manifest_is_valid_json(self):
        resp = await self.client.get("/manifest.json")
        body = await resp.text()
        data = json.loads(body)
        assert "name" in data
        assert "start_url" in data
        assert "display" in data

    @unittest_run_loop
    async def test_manifest_content_type(self):
        resp = await self.client.get("/manifest.json")
        # Accept either application/manifest+json or application/json
        assert "json" in resp.content_type

    # ── /sw.js ──────────────────────────────────────────────────────

    @unittest_run_loop
    async def test_sw_returns_200(self):
        resp = await self.client.get("/sw.js")
        assert resp.status == 200

    @unittest_run_loop
    async def test_sw_content_type_javascript(self):
        resp = await self.client.get("/sw.js")
        assert "javascript" in resp.content_type

    @unittest_run_loop
    async def test_sw_contains_cache_logic(self):
        resp = await self.client.get("/sw.js")
        body = await resp.text()
        assert "install" in body
        assert "fetch" in body

    # ── /config ─────────────────────────────────────────────────────

    @unittest_run_loop
    async def test_config_returns_200(self):
        resp = await self.client.get("/config")
        assert resp.status == 200

    @unittest_run_loop
    async def test_config_contains_expected_keys(self):
        resp = await self.client.get("/config")
        data = await resp.json()
        assert "check_interval" in data
        assert "quiet_hours_start" in data
        assert "quiet_hours_end" in data
        assert "parking_zone_top" in data
        assert "parking_zone_bottom" in data
        assert "parking_zone_left" in data
        assert "parking_zone_right" in data
        assert "scan_positions" in data
        assert "confidence_threshold" in data
        assert "home_position" in data
        assert "api_port" in data

    @unittest_run_loop
    async def test_config_correct_values(self):
        resp = await self.client.get("/config")
        data = await resp.json()
        assert data["check_interval"] == 120
        assert data["home_position"] == 0
        assert data["confidence_threshold"] == "medium"
        assert data["scan_positions"] == [-30, 0, 30]

    @unittest_run_loop
    async def test_config_does_not_expose_secrets(self):
        resp = await self.client.get("/config")
        data = await resp.json()
        # These fields must NEVER appear in the /config response
        secret_fields = [
            "tapo_password", "TAPO_PASSWORD",
            "anthropic_api_key", "ANTHROPIC_API_KEY",
            "telegram_bot_token", "TELEGRAM_BOT_TOKEN",
            "api_key", "API_KEY",
            "pushover_user_key", "PUSHOVER_USER_KEY",
            "pushover_api_token", "PUSHOVER_API_TOKEN",
        ]
        for field in secret_fields:
            assert field not in data, f"Secret field '{field}' must not be in /config response"

    # ── /history ────────────────────────────────────────────────────

    @unittest_run_loop
    async def test_history_returns_200(self):
        resp = await self.client.get("/history")
        assert resp.status == 200

    @unittest_run_loop
    async def test_history_structure(self):
        resp = await self.client.get("/history")
        data = await resp.json()
        assert "hours" in data
        assert "stats" in data

    @unittest_run_loop
    async def test_history_hours_count(self):
        resp = await self.client.get("/history")
        data = await resp.json()
        assert len(data["hours"]) == 24

    @unittest_run_loop
    async def test_history_hour_fields(self):
        resp = await self.client.get("/history")
        data = await resp.json()
        hour = data["hours"][0]
        assert "hour" in hour
        assert "total" in hour
        assert "free" in hour
        assert "occupied" in hour
        assert "free_percentage" in hour

    @unittest_run_loop
    async def test_history_stats_fields(self):
        resp = await self.client.get("/history")
        data = await resp.json()
        stats = data["stats"]
        assert "total_checks" in stats
        assert "free_percentage" in stats
        assert "occupied_percentage" in stats

    # ── Auth middleware exemptions ───────────────────────────────────

    @unittest_run_loop
    async def test_dashboard_exempt_from_auth(self):
        """Dashboard should be accessible without API key even when auth is configured."""
        api_module._config.API_KEY = "secret-key"
        try:
            resp = await self.client.get("/dashboard")
            assert resp.status == 200
        finally:
            api_module._config.API_KEY = ""

    @unittest_run_loop
    async def test_manifest_exempt_from_auth(self):
        api_module._config.API_KEY = "secret-key"
        try:
            resp = await self.client.get("/manifest.json")
            assert resp.status == 200
        finally:
            api_module._config.API_KEY = ""

    @unittest_run_loop
    async def test_sw_exempt_from_auth(self):
        api_module._config.API_KEY = "secret-key"
        try:
            resp = await self.client.get("/sw.js")
            assert resp.status == 200
        finally:
            api_module._config.API_KEY = ""

    @unittest_run_loop
    async def test_config_requires_auth_when_configured(self):
        api_module._config.API_KEY = "secret-key"
        try:
            resp = await self.client.get("/config")
            assert resp.status == 401
        finally:
            api_module._config.API_KEY = ""

    @unittest_run_loop
    async def test_config_accessible_with_api_key(self):
        api_module._config.API_KEY = "secret-key"
        try:
            resp = await self.client.get("/config", headers={"X-API-Key": "secret-key"})
            assert resp.status == 200
        finally:
            api_module._config.API_KEY = ""

    @unittest_run_loop
    async def test_history_requires_auth_when_configured(self):
        api_module._config.API_KEY = "secret-key"
        try:
            resp = await self.client.get("/history")
            assert resp.status == 401
        finally:
            api_module._config.API_KEY = ""
