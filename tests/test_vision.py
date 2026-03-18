"""
tests/test_vision.py — Tests for vision.py
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from config import Config
from vision import ParkingVision, _FALLBACK_RESPONSE


@pytest.fixture
def cfg():
    return Config(
        ANTHROPIC_API_KEY="sk-ant-test",
        CLAUDE_MODEL="claude-sonnet-4-20250514",
        CLAUDE_MAX_TOKENS=1024,
        PARKING_ZONE_TOP=30,
        PARKING_ZONE_BOTTOM=80,
        PARKING_ZONE_LEFT=20,
        PARKING_ZONE_RIGHT=80,
    )


@pytest.fixture
def vision(cfg):
    with patch("vision.anthropic.Anthropic"):
        return ParkingVision(cfg)


class TestParseResponse:
    def test_parse_valid_json(self, vision):
        raw = '{"status": "FREE", "confidence": "high", "description": "No cars visible."}'
        result = vision._parse_response(raw)
        assert result["status"] == "FREE"
        assert result["confidence"] == "high"
        assert "No cars visible" in result["description"]

    def test_parse_occupied_json(self, vision):
        raw = '{"status": "OCCUPIED", "confidence": "medium", "description": "A blue car is parked."}'
        result = vision._parse_response(raw)
        assert result["status"] == "OCCUPIED"
        assert result["confidence"] == "medium"

    def test_parse_json_in_markdown_fence(self, vision):
        raw = '```json\n{"status": "FREE", "confidence": "low", "description": "Possibly free."}\n```'
        result = vision._parse_response(raw)
        assert result["status"] == "FREE"

    def test_parse_json_in_plain_fence(self, vision):
        raw = '```\n{"status": "OCCUPIED", "confidence": "high", "description": "Car present."}\n```'
        result = vision._parse_response(raw)
        assert result["status"] == "OCCUPIED"

    def test_parse_json_embedded_in_text(self, vision):
        raw = 'Here is my analysis: {"status": "FREE", "confidence": "medium", "description": "Empty space."} Hope that helps!'
        result = vision._parse_response(raw)
        assert result["status"] == "FREE"

    def test_parse_malformed_returns_fallback(self, vision):
        raw = "I cannot determine the parking status from this image."
        result = vision._parse_response(raw)
        assert result["status"] == "UNKNOWN"
        assert result["confidence"] == "low"
        assert "Failed to parse" in result["description"]

    def test_parse_empty_string_returns_fallback(self, vision):
        result = vision._parse_response("")
        assert result["status"] == "UNKNOWN"

    def test_normalise_invalid_status(self, vision):
        raw = '{"status": "MAYBE", "confidence": "high", "description": "Unclear."}'
        result = vision._parse_response(raw)
        assert result["status"] == "UNKNOWN"

    def test_normalise_invalid_confidence(self, vision):
        raw = '{"status": "FREE", "confidence": "very high", "description": "Clear."}'
        result = vision._parse_response(raw)
        assert result["confidence"] == "low"  # invalid → default to low

    def test_status_case_insensitive(self, vision):
        raw = '{"status": "free", "confidence": "high", "description": "Empty."}'
        result = vision._parse_response(raw)
        assert result["status"] == "FREE"


class TestPromptBuilding:
    def test_home_prompt_includes_zone(self, vision):
        prompt = vision._build_home_prompt()
        assert "30" in prompt  # PARKING_ZONE_TOP
        assert "80" in prompt  # PARKING_ZONE_BOTTOM
        assert "20" in prompt  # PARKING_ZONE_LEFT
        assert "UK terraced street" in prompt

    def test_home_prompt_mentions_reflections(self, vision):
        prompt = vision._build_home_prompt()
        assert "reflection" in prompt.lower() or "glare" in prompt.lower()

    def test_home_prompt_requests_json(self, vision):
        prompt = vision._build_home_prompt()
        assert "FREE" in prompt
        assert "OCCUPIED" in prompt
        assert "confidence" in prompt

    def test_scan_prompt_includes_position(self, vision):
        prompt = vision._build_scan_prompt("far left")
        assert "far left" in prompt
        assert "FREE" in prompt


class TestCheckHomeSpot:
    def test_check_home_spot_returns_dict(self, vision):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"status": "FREE", "confidence": "high", "description": "Clear."}')]
        vision.client.messages.create.return_value = mock_response

        result = vision.check_home_spot(b"fake-image-bytes")
        assert result["status"] == "FREE"
        assert result["confidence"] == "high"

    def test_check_home_spot_handles_auth_error(self, vision):
        import anthropic
        vision.client.messages.create.side_effect = anthropic.AuthenticationError(
            message="Invalid key", response=MagicMock(status_code=401, headers={}), body={}
        )
        result = vision.check_home_spot(b"fake")
        assert result["status"] == "UNKNOWN"
        assert "Authentication" in result["description"]

    def test_check_scan_position_returns_dict(self, vision):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"status": "OCCUPIED", "confidence": "medium", "description": "Cars present."}')]
        vision.client.messages.create.return_value = mock_response

        result = vision.check_scan_position(b"fake-image-bytes", "left")
        assert result["status"] == "OCCUPIED"
