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
        CLAUDE_MODEL="claude-sonnet-4-5",
        CLAUDE_MODEL_FAST="claude-haiku-3-5-20241022",
        CLAUDE_MAX_TOKENS=150,
        PARKING_ZONE_TOP=30,
        PARKING_ZONE_BOTTOM=80,
        PARKING_ZONE_LEFT=20,
        PARKING_ZONE_RIGHT=80,
        VEHICLE_LENGTH_METRES=4.5,
        MIN_SPACE_METRES=5.0,
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

    def test_home_prompt_mentions_near_side(self, vision):
        prompt = vision._build_home_prompt()
        assert "near side" in prompt.lower() or "near" in prompt.lower()

    def test_home_prompt_mentions_double_yellow(self, vision):
        prompt = vision._build_home_prompt()
        assert "double yellow" in prompt.lower()

    def test_home_prompt_mentions_moving_traffic(self, vision):
        prompt = vision._build_home_prompt()
        assert "moving" in prompt.lower() or "traffic" in prompt.lower()

    def test_home_prompt_mentions_vehicle_size(self, vision):
        prompt = vision._build_home_prompt()
        assert "mid-size suv" in prompt.lower() or "4.5" in prompt

    def test_home_prompt_mentions_min_space(self, vision):
        prompt = vision._build_home_prompt()
        assert "5.0" in prompt or "5 metres" in prompt.lower()

    def test_scan_prompt_includes_position(self, vision):
        prompt = vision._build_scan_prompt("far left")
        assert "far left" in prompt
        assert "FREE" in prompt

    def test_scan_prompt_mentions_near_side(self, vision):
        prompt = vision._build_scan_prompt("left")
        assert "near side" in prompt.lower() or "near" in prompt.lower()

    def test_scan_prompt_mentions_double_yellow(self, vision):
        prompt = vision._build_scan_prompt("left")
        assert "double yellow" in prompt.lower()

    def test_scan_prompt_mentions_moving_vehicles(self, vision):
        prompt = vision._build_scan_prompt("centre")
        assert "moving" in prompt.lower() or "stationary" in prompt.lower()

    def test_scan_prompt_mentions_vehicle_size(self, vision):
        prompt = vision._build_scan_prompt("right")
        assert "mid-size suv" in prompt.lower() or "4.5" in prompt


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

    def test_check_home_spot_uses_fast_model_when_flag_set(self, vision):
        """use_fast_model=True should pass CLAUDE_MODEL_FAST to the API."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"status": "FREE", "confidence": "high", "description": "Clear."}')]
        vision.client.messages.create.return_value = mock_response

        vision.check_home_spot(b"fake", use_fast_model=True)

        call_kwargs = vision.client.messages.create.call_args
        assert call_kwargs[1]["model"] == vision.config.CLAUDE_MODEL_FAST

    def test_check_home_spot_uses_default_model_when_flag_false(self, vision):
        """use_fast_model=False (default) should use CLAUDE_MODEL (Sonnet)."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"status": "FREE", "confidence": "high", "description": "Clear."}')]
        vision.client.messages.create.return_value = mock_response

        vision.check_home_spot(b"fake", use_fast_model=False)

        call_kwargs = vision.client.messages.create.call_args
        assert call_kwargs[1]["model"] == vision.config.CLAUDE_MODEL

    def test_check_home_spot_default_does_not_use_fast_model(self, vision):
        """Calling check_home_spot() without the flag should default to CLAUDE_MODEL."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"status": "FREE", "confidence": "high", "description": "Clear."}')]
        vision.client.messages.create.return_value = mock_response

        vision.check_home_spot(b"fake")

        call_kwargs = vision.client.messages.create.call_args
        assert call_kwargs[1]["model"] == vision.config.CLAUDE_MODEL

    def test_check_scan_position_always_uses_default_model(self, vision):
        """check_scan_position() should never use the fast model."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"status": "FREE", "confidence": "high", "description": "Clear."}')]
        vision.client.messages.create.return_value = mock_response

        vision.check_scan_position(b"fake", "left")

        call_kwargs = vision.client.messages.create.call_args
        assert call_kwargs[1]["model"] == vision.config.CLAUDE_MODEL


class TestCalibrationAssessment:
    def test_parse_calibration_valid_json(self, vision):
        raw = json.dumps({
            "street_visible": True,
            "parking_area_visible": True,
            "parking_side": "near",
            "opposite_restriction": "double_yellow",
            "obstructions": ["none"],
            "home_spot_visible": True,
            "usefulness_score": 8,
            "description": "Clear view of near-side kerbside parking.",
        })
        result = vision._parse_calibration_response(raw)
        assert result["street_visible"] is True
        assert result["parking_side"] == "near"
        assert result["usefulness_score"] == 8
        assert result["home_spot_visible"] is True

    def test_parse_calibration_clamps_score(self, vision):
        raw = json.dumps({
            "street_visible": True,
            "parking_area_visible": True,
            "parking_side": "near",
            "opposite_restriction": "none",
            "obstructions": ["none"],
            "home_spot_visible": False,
            "usefulness_score": 999,
            "description": "Test.",
        })
        result = vision._parse_calibration_response(raw)
        assert result["usefulness_score"] == 10

    def test_parse_calibration_invalid_parking_side(self, vision):
        raw = json.dumps({
            "street_visible": False,
            "parking_area_visible": False,
            "parking_side": "INVALID",
            "opposite_restriction": "unclear",
            "obstructions": ["none"],
            "home_spot_visible": False,
            "usefulness_score": 2,
            "description": "Blocked.",
        })
        result = vision._parse_calibration_response(raw)
        assert result["parking_side"] == "none"

    def test_parse_calibration_unparseable_returns_fallback(self, vision):
        from vision import _CALIBRATION_FALLBACK
        result = vision._parse_calibration_response("This is not JSON at all.")
        assert result == dict(_CALIBRATION_FALLBACK)

    def test_calibration_prompt_contains_angle(self, vision):
        prompt = vision._build_calibration_prompt(45)
        assert "+45" in prompt or "45" in prompt

    def test_calibration_prompt_negative_angle(self, vision):
        prompt = vision._build_calibration_prompt(-30)
        assert "-30" in prompt

    def test_assess_calibration_frame_returns_dict(self, vision):
        raw = json.dumps({
            "street_visible": True,
            "parking_area_visible": True,
            "parking_side": "near",
            "opposite_restriction": "double_yellow",
            "obstructions": ["none"],
            "home_spot_visible": False,
            "usefulness_score": 7,
            "description": "Street visible.",
        })
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=raw)]
        vision.client.messages.create.return_value = mock_response

        result = vision.assess_calibration_frame(b"fake-image", 15)
        assert result["usefulness_score"] == 7
        assert result["parking_side"] == "near"
