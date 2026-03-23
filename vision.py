"""
vision.py — Claude AI vision analysis for parking space detection.

Sends camera frames to Anthropic Claude and parses structured parking status responses.
"""

import base64
import json
import logging
import re
from typing import Optional

import anthropic

from config import Config

logger = logging.getLogger(__name__)

_FALLBACK_RESPONSE = {
    "status": "UNKNOWN",
    "confidence": "low",
    "description": "Failed to parse AI response",
}

_CALIBRATION_FALLBACK = {
    "street_visible": False,
    "parking_area_visible": False,
    "parking_side": "none",
    "opposite_restriction": "unclear",
    "obstructions": ["none"],
    "home_spot_visible": False,
    "usefulness_score": 0,
    "description": "Failed to assess calibration frame",
}


class ParkingVision:
    """Uses Claude vision API to analyse parking space images."""

    def __init__(self, config: Config, cost_tracker=None) -> None:
        """
        Initialise with configuration and create the Anthropic client.

        Args:
            config: Application configuration.
            cost_tracker: Optional CostTracker instance for recording API usage.
        """
        self.config = config
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self._cost_tracker = cost_tracker

    # ------------------------------------------------------------------
    # Public analysis methods
    # ------------------------------------------------------------------

    def check_home_spot(
        self,
        image_bytes: bytes,
        use_fast_model: bool = False,
        model_override: Optional[str] = None,
    ) -> dict:
        """
        Analyse an image for the home parking spot status.

        Args:
            image_bytes:    JPEG image data (ideally pre-processed via
                            camera.prepare_for_vision()).
            use_fast_model: If True, use CLAUDE_MODEL_FAST (Haiku) instead of
                            CLAUDE_MODEL (Sonnet).  Use True for background
                            monitoring; False (default) for on-demand requests.
            model_override: Explicit model ID that takes priority over the above.

        Returns:
            Dict with keys: status ("FREE"/"OCCUPIED"/"UNKNOWN"),
            confidence ("high"/"medium"/"low"), description (str).
        """
        prompt = self._build_home_prompt()
        if model_override:
            model = model_override
        elif use_fast_model:
            model = self.config.CLAUDE_MODEL_FAST
        else:
            model = self.config.CLAUDE_MODEL
        try:
            raw_text, usage = self._send_to_claude(image_bytes, prompt, model=model)
            if usage and self._cost_tracker:
                try:
                    self._cost_tracker.record_call(
                        model=model,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        check_type="home",
                    )
                except Exception as exc:
                    logger.debug("Cost tracking error: %s", exc)
            result = self._parse_response(raw_text)
            logger.info(
                "Home spot check: status=%s confidence=%s — %s",
                result.get("status"),
                result.get("confidence"),
                result.get("description"),
            )
            return result
        except anthropic.AuthenticationError as exc:
            logger.error("Claude authentication error — check ANTHROPIC_API_KEY: %s", exc)
            return {**_FALLBACK_RESPONSE, "description": "Authentication error"}
        except anthropic.RateLimitError as exc:
            logger.warning("Claude rate limit hit — will retry next cycle: %s", exc)
            return {**_FALLBACK_RESPONSE, "description": "Rate limit — try again shortly"}
        except anthropic.APITimeoutError as exc:
            logger.warning("Claude API timeout: %s", exc)
            return {**_FALLBACK_RESPONSE, "description": "API timeout"}
        except Exception as exc:
            logger.error("Unexpected error calling Claude API: %s", exc)
            return {**_FALLBACK_RESPONSE, "description": f"API error: {exc}"}

    def check_scan_position(self, image_bytes: bytes, position_name: str) -> dict:
        """
        Analyse an image for any visible free spaces at a given street position.

        Args:
            image_bytes: JPEG image data.
            position_name: Human-readable position label (e.g. "far left").

        Returns:
            Dict with keys: status, confidence, description.
        """
        prompt = self._build_scan_prompt(position_name)
        model = self.config.CLAUDE_MODEL
        try:
            raw_text, usage = self._send_to_claude(image_bytes, prompt, model=model)
            if usage and self._cost_tracker:
                try:
                    self._cost_tracker.record_call(
                        model=model,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        check_type="scan",
                    )
                except Exception as exc:
                    logger.debug("Cost tracking error: %s", exc)
            result = self._parse_response(raw_text)
            logger.info(
                "Scan position '%s': status=%s confidence=%s",
                position_name,
                result.get("status"),
                result.get("confidence"),
            )
            return result
        except anthropic.AuthenticationError as exc:
            logger.error("Claude authentication error: %s", exc)
            return {**_FALLBACK_RESPONSE, "description": "Authentication error"}
        except anthropic.RateLimitError as exc:
            logger.warning("Claude rate limit at position '%s': %s", position_name, exc)
            return {**_FALLBACK_RESPONSE, "description": "Rate limit — try again shortly"}
        except anthropic.APITimeoutError as exc:
            logger.warning("Claude API timeout at position '%s': %s", position_name, exc)
            return {**_FALLBACK_RESPONSE, "description": "API timeout"}
        except Exception as exc:
            logger.error("Unexpected error calling Claude API for scan position '%s': %s", position_name, exc)
            return {**_FALLBACK_RESPONSE, "description": f"API error: {exc}"}

    # ------------------------------------------------------------------
    # Calibration assessment
    # ------------------------------------------------------------------

    def assess_calibration_frame(self, image_bytes: bytes, angle: int) -> dict:
        """
        Ask Claude to assess a calibration frame for parking monitoring usefulness.

        Args:
            image_bytes: JPEG image data.
            angle:       Pan angle at which the frame was captured (degrees).

        Returns:
            Dict with street_visible, parking_area_visible, parking_side,
            opposite_restriction, obstructions, home_spot_visible,
            usefulness_score (0–10), description.
        """
        prompt = self._build_calibration_prompt(angle)
        model = self.config.CLAUDE_MODEL
        try:
            raw_text, usage = self._send_to_claude(image_bytes, prompt, model=model)
            if usage and self._cost_tracker:
                try:
                    self._cost_tracker.record_call(
                        model=model,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        check_type="scan",
                    )
                except Exception as exc:
                    logger.debug("Cost tracking error: %s", exc)
            result = self._parse_calibration_response(raw_text)
            logger.info(
                "Calibration angle %+d°: usefulness=%d home_spot=%s — %s",
                angle,
                result.get("usefulness_score", 0),
                result.get("home_spot_visible", False),
                result.get("description", ""),
            )
            return result
        except anthropic.AuthenticationError as exc:
            logger.error("Claude authentication error — check ANTHROPIC_API_KEY: %s", exc)
            return dict(_CALIBRATION_FALLBACK)
        except anthropic.RateLimitError as exc:
            logger.warning("Claude rate limit at angle %+d°: %s", angle, exc)
            return dict(_CALIBRATION_FALLBACK)
        except anthropic.APITimeoutError as exc:
            logger.warning("Claude API timeout at angle %+d°: %s", angle, exc)
            return dict(_CALIBRATION_FALLBACK)
        except Exception as exc:
            logger.error("Unexpected error calling Claude at angle %+d°: %s", angle, exc)
            return dict(_CALIBRATION_FALLBACK)

    def _build_calibration_prompt(self, angle: int) -> str:
        """Build the prompt for calibration frame assessment."""
        return (
            "You are helping calibrate a parking monitoring camera. This camera is mounted "
            "inside a house window looking out at a street. "
            f"This frame was captured at pan angle {angle:+d}°.\n\n"
            "Assess this image for its usefulness as a parking monitoring viewpoint.\n\n"
            "Respond with ONLY valid JSON (no markdown, no explanation):\n"
            "{\n"
            '  "street_visible": true or false,\n'
            '  "parking_area_visible": true or false,\n'
            '  "parking_side": "near" or "far" or "both" or "none",\n'
            '  "opposite_restriction": "double_yellow" or "single_yellow" or "none" or "unclear",\n'
            '  "obstructions": ["window_frame", "wall", "reflection", "too_dark", "none"],\n'
            '  "home_spot_visible": true or false,\n'
            '  "usefulness_score": 0-10,\n'
            '  "description": "one sentence describing what you see"\n'
            "}\n\n"
            "Scoring guide:\n"
            "- 10: Clear view of kerbside parking, minimal obstructions\n"
            "- 7-9: Good parking view with minor issues\n"
            "- 4-6: Partially useful, some obstructions or only partial street view\n"
            "- 1-3: Mostly obstructed (window frame, wall, heavy reflections)\n"
            "- 0: No street visible at all"
        )

    def _parse_calibration_response(self, text: str) -> dict:
        """
        Parse and validate a calibration assessment response from Claude.

        Applies sensible defaults for any missing or invalid fields.

        Returns:
            Dict with the full calibration assessment.
        """
        stripped = text.strip()
        data = None

        # 1. Try raw JSON
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            pass

        # 2. Try ```json ... ``` or ``` ... ``` fences — use greedy match to
        #    capture the entire JSON block including any nested structures.
        if data is None:
            fence_pattern = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)
            match = fence_pattern.search(stripped)
            if match:
                try:
                    data = json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass

        # 3. Try any {...} block with a calibration-relevant key
        if data is None:
            brace_pattern = re.compile(r"\{[^{}]*\}", re.DOTALL)
            for m in brace_pattern.finditer(stripped):
                try:
                    candidate = json.loads(m.group(0))
                    if "usefulness_score" in candidate or "street_visible" in candidate:
                        data = candidate
                        break
                except json.JSONDecodeError:
                    continue

        if data is None:
            logger.warning(
                "Could not parse calibration response as JSON: %s", text[:300]
            )
            return dict(_CALIBRATION_FALLBACK)

        # Normalise fields with sensible defaults
        street_visible = bool(data.get("street_visible", False))
        parking_area_visible = bool(data.get("parking_area_visible", False))

        parking_side = str(data.get("parking_side", "none")).lower()
        if parking_side not in ("near", "far", "both", "none"):
            parking_side = "none"

        opposite_restriction = str(data.get("opposite_restriction", "unclear")).lower()
        if opposite_restriction not in ("double_yellow", "single_yellow", "none", "unclear"):
            opposite_restriction = "unclear"

        obstructions = data.get("obstructions", ["none"])
        if not isinstance(obstructions, list):
            obstructions = ["none"]

        home_spot_visible = bool(data.get("home_spot_visible", False))

        usefulness_score = data.get("usefulness_score", 0)
        try:
            usefulness_score = max(0, min(10, int(usefulness_score)))
        except (ValueError, TypeError):
            usefulness_score = 0

        description = str(data.get("description", "No description provided"))

        return {
            "street_visible": street_visible,
            "parking_area_visible": parking_area_visible,
            "parking_side": parking_side,
            "opposite_restriction": opposite_restriction,
            "obstructions": obstructions,
            "home_spot_visible": home_spot_visible,
            "usefulness_score": usefulness_score,
            "description": description,
        }

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    def _build_home_prompt(self) -> str:
        """Build the prompt for checking the home parking spot."""
        zone = self.config
        parking_side = zone.STREET_PARKING_SIDE  # "near" or "far"
        opposite_restriction = zone.OPPOSITE_SIDE_RESTRICTION

        if parking_side == "far":
            near_side_desc = (
                "parking is ONLY on the FAR side of the road "
                "(opposite to the camera, closest to the top of the image)"
            )
            near_side_focus = "FAR side of the road (opposite to the camera)"
        else:
            near_side_desc = (
                "parking is ONLY on the NEAR side "
                "(the same side as the camera/house, closest to the bottom of the image)"
            )
            near_side_focus = "NEAR side of the road (camera's side)"

        restriction_map = {
            "double_yellow": "DOUBLE YELLOW LINES — no parking is allowed there",
            "single_yellow": "SINGLE YELLOW LINES — parking restrictions apply at certain times",
            "no_parking": "NO PARKING signs — no parking is allowed there",
            "none": "no parking restrictions on that side",
        }
        opposite_desc = restriction_map.get(
            opposite_restriction,
            f"{opposite_restriction.replace('_', ' ').upper()} — check local restrictions",
        )

        return (
            "You are analysing a photo taken through a house window overlooking a UK terraced street.\n\n"
            "STREET LAYOUT:\n"
            f"- This is a one-sided parking street; {near_side_desc}.\n"
            f"- The OPPOSITE side of the road has {opposite_desc}. Completely ignore any vehicles on that side.\n"
            "- The road surface runs through the middle of the image.\n\n"
            "IMPORTANT — IGNORE:\n"
            "- Window glass reflections, glare, condensation, or smears\n"
            "- Interior objects reflected in the window\n"
            "- Foreground objects: stone wall, wheelie bins, garden — these are below/in front of the road\n"
            "- Window frame edges (visible at extreme pan angles)\n"
            "- MOVING TRAFFIC: any vehicles driving along the road (not parked). "
            "Only stationary vehicles parked against the kerb on the near side matter.\n"
            "- Vehicles on the OPPOSITE side of the road (irrelevant — see restriction above)\n\n"
            "FOCUS ON:\n"
            f"- The kerbside parking area on the {near_side_focus}\n"
            f"- The parking zone: approximately top {zone.PARKING_ZONE_TOP}% to {zone.PARKING_ZONE_BOTTOM}% "
            f"of the image height, left {zone.PARKING_ZONE_LEFT}% to {zone.PARKING_ZONE_RIGHT}% "
            "of the image width\n"
            "- Whether a vehicle (car, van, SUV, lorry, motorcycle) is PARKED and STATIONARY "
            "against the kerb on the near side\n\n"
            "VEHICLE SIZE CONTEXT:\n"
            f"- The owner drives a mid-size SUV (~{zone.VEHICLE_LENGTH_METRES}m long). "
            "A space is 'FREE' even if it's a tight fit, as long as the vehicle could physically fit.\n"
            f"- Gaps of ~{zone.MIN_SPACE_METRES} metres or more between parked cars count as FREE.\n\n"
            "Determine if the parking space directly in front of the house (near side) is FREE or OCCUPIED.\n\n"
            "Respond with ONLY valid JSON (no markdown, no explanation):\n"
            '{"status": "FREE" or "OCCUPIED", "confidence": "high" or "medium" or "low", '
            '"description": "one sentence describing what you see on the near side kerb"}'
        )

    def _build_scan_prompt(self, position_name: str) -> str:
        """Build the prompt for a street scan position."""
        cfg = self.config
        parking_side = cfg.STREET_PARKING_SIDE  # "near" or "far"
        opposite_restriction = cfg.OPPOSITE_SIDE_RESTRICTION

        if parking_side == "far":
            near_side_desc = "FAR side of the road (opposite to the camera, closest to top of image)"
        else:
            near_side_desc = "NEAR side of the road (camera's side, closest to bottom of image)"

        restriction_map = {
            "double_yellow": "DOUBLE YELLOW LINES",
            "single_yellow": "SINGLE YELLOW LINES",
            "no_parking": "NO PARKING signs",
            "none": "no restriction",
        }
        opposite_desc = restriction_map.get(
            opposite_restriction,
            opposite_restriction.replace("_", " ").upper(),
        )

        return (
            f"You are analysing a photo of the {position_name} section of a UK terraced street, "
            "taken through a house window.\n\n"
            "STREET LAYOUT:\n"
            f"- Parking is ONLY allowed on the {near_side_desc}.\n"
            f"- The OPPOSITE side has {opposite_desc} — completely ignore it.\n"
            "- The road runs through the middle — ignore moving traffic.\n\n"
            "IMPORTANT — IGNORE:\n"
            "- Window glass reflections, glare, condensation\n"
            "- Interior reflections and foreground objects (stone wall, bins, garden)\n"
            "- Window frame edges at extreme angles\n"
            "- MOVING VEHICLES on the road — only stationary parked cars on the near kerb matter\n"
            f"- Any vehicles on the opposite side of the road ({opposite_desc})\n\n"
            "FOCUS ON:\n"
            f"- The kerbside parking spaces on the {near_side_desc} only\n"
            f"- Whether there are any gaps between parked cars where a mid-size SUV "
            f"(~{cfg.VEHICLE_LENGTH_METRES}m) could fit\n"
            f"- A gap of ~{cfg.MIN_SPACE_METRES} metres or more counts as a free space\n"
            "- Count the approximate number of free spaces visible\n\n"
            "Determine if there is any FREE parking space visible on the near side of the street.\n\n"
            "Respond with ONLY valid JSON (no markdown, no explanation):\n"
            '{"status": "FREE" or "OCCUPIED", "confidence": "high" or "medium" or "low", '
            '"description": "one sentence describing what you see, including approximate number of free spaces if any"}'
        )

    # ------------------------------------------------------------------
    # Claude API call
    # ------------------------------------------------------------------

    def _send_to_claude(self, image_bytes: bytes, prompt: str, model: Optional[str] = None):
        """
        Send an image and prompt to Claude, returning the response text and usage.

        Args:
            image_bytes: Raw JPEG image data.
            prompt: Text prompt for Claude.
            model: Override the model for this call.  Defaults to CLAUDE_MODEL.

        Returns:
            Tuple of (response_text: str, usage: object | None).
        """
        b64_image = base64.standard_b64encode(image_bytes).decode("utf-8")

        use_model = model if model is not None else self.config.CLAUDE_MODEL

        message = self.client.messages.create(
            model=use_model,
            max_tokens=self.config.CLAUDE_MAX_TOKENS,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": b64_image,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt,
                        },
                    ],
                }
            ],
        )

        response_text = message.content[0].text
        logger.debug("Claude raw response: %s", response_text[:200])
        usage = getattr(message, "usage", None)
        return response_text, usage

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, text: str) -> dict:
        """
        Extract a JSON dict from Claude's response.

        Handles:
        - Raw JSON
        - JSON wrapped in ```json ... ``` fences
        - JSON wrapped in ``` ... ``` fences

        Returns:
            Dict with status, confidence, description keys.
            Falls back to _FALLBACK_RESPONSE on parse failure.
        """
        # 1. Try raw JSON first
        stripped = text.strip()
        try:
            data = json.loads(stripped)
            return self._normalise_response(data)
        except json.JSONDecodeError:
            pass

        # 2. Try extracting from ```json ... ``` or ``` ... ``` fences
        fence_pattern = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)
        match = fence_pattern.search(stripped)
        if match:
            try:
                data = json.loads(match.group(1))
                return self._normalise_response(data)
            except json.JSONDecodeError:
                pass

        # 3. Try finding any {...} block in the text
        brace_pattern = re.compile(r"\{[^{}]*\}", re.DOTALL)
        for m in brace_pattern.finditer(stripped):
            try:
                data = json.loads(m.group(0))
                if "status" in data:
                    return self._normalise_response(data)
            except json.JSONDecodeError:
                continue

        logger.warning("Could not parse Claude response as JSON: %s", text[:300])
        return dict(_FALLBACK_RESPONSE)

    def _normalise_response(self, data: dict) -> dict:
        """Ensure response dict has the required keys with valid values."""
        status = str(data.get("status", "UNKNOWN")).upper()
        if status not in ("FREE", "OCCUPIED"):
            status = "UNKNOWN"

        confidence = str(data.get("confidence", "low")).lower()
        if confidence not in ("high", "medium", "low"):
            confidence = "low"

        description = str(data.get("description", "No description provided"))

        return {
            "status": status,
            "confidence": confidence,
            "description": description,
        }
