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


class ParkingVision:
    """Uses Claude vision API to analyse parking space images."""

    def __init__(self, config: Config) -> None:
        """Initialise with configuration and create the Anthropic client."""
        self.config = config
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    # ------------------------------------------------------------------
    # Public analysis methods
    # ------------------------------------------------------------------

    def check_home_spot(self, image_bytes: bytes) -> dict:
        """
        Analyse an image for the home parking spot status.

        Args:
            image_bytes: JPEG image data.

        Returns:
            Dict with keys: status ("FREE"/"OCCUPIED"/"UNKNOWN"),
            confidence ("high"/"medium"/"low"), description (str).
        """
        prompt = self._build_home_prompt()
        try:
            raw_text = self._send_to_claude(image_bytes, prompt)
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
        try:
            raw_text = self._send_to_claude(image_bytes, prompt)
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
    # Prompt builders
    # ------------------------------------------------------------------

    def _build_home_prompt(self) -> str:
        """Build the prompt for checking the home parking spot."""
        zone = self.config
        return (
            "You are analysing a photo taken through a window of a UK terraced street.\n\n"
            "IMPORTANT — IGNORE:\n"
            "- Window glass reflections, glare, or any reflections in the glass\n"
            "- Interior objects reflected in the window\n"
            "- Streaks, smears, or condensation on the glass\n\n"
            "FOCUS ON:\n"
            "- The road surface and kerbside parking area visible through the glass\n"
            f"- The parking zone: top {zone.PARKING_ZONE_TOP}% to {zone.PARKING_ZONE_BOTTOM}% "
            f"of the image height, left {zone.PARKING_ZONE_LEFT}% to {zone.PARKING_ZONE_RIGHT}% "
            "of the image width\n"
            "- Whether a vehicle (car, van, lorry, motorcycle) is parked there\n\n"
            "Determine if the parking space directly in front of the house is FREE or OCCUPIED.\n\n"
            "Respond with ONLY valid JSON (no markdown, no explanation):\n"
            '{"status": "FREE" or "OCCUPIED", "confidence": "high" or "medium" or "low", '
            '"description": "one sentence describing what you see"}'
        )

    def _build_scan_prompt(self, position_name: str) -> str:
        """Build the prompt for a street scan position."""
        return (
            f"You are analysing a photo of the {position_name} section of a UK terraced street, "
            "taken through a window.\n\n"
            "IMPORTANT — IGNORE:\n"
            "- Window glass reflections, glare, or any reflections in the glass\n"
            "- Interior objects reflected in the window\n"
            "- Streaks, smears, or condensation on the glass\n\n"
            "FOCUS ON:\n"
            "- The road surface and kerbside areas visible through the glass\n"
            "- Whether there are any FREE (empty) parking spaces visible\n"
            "- How many cars are present versus how many spaces appear free\n\n"
            "Determine if there is any FREE parking space visible in this part of the street.\n\n"
            "Respond with ONLY valid JSON (no markdown, no explanation):\n"
            '{"status": "FREE" or "OCCUPIED", "confidence": "high" or "medium" or "low", '
            '"description": "one sentence describing what you see, including approximate number of free spaces if any"}'
        )

    # ------------------------------------------------------------------
    # Claude API call
    # ------------------------------------------------------------------

    def _send_to_claude(self, image_bytes: bytes, prompt: str) -> str:
        """
        Send an image and prompt to Claude, returning the response text.

        Args:
            image_bytes: Raw JPEG image data.
            prompt: Text prompt for Claude.

        Returns:
            Claude's response as a string.
        """
        b64_image = base64.standard_b64encode(image_bytes).decode("utf-8")

        message = self.client.messages.create(
            model=self.config.CLAUDE_MODEL,
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
        return response_text

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
        fence_pattern = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
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
