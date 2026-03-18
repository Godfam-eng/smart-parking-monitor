"""
vision.py — Claude AI image analysis for parking space detection.

Sends JPEG frames to the Anthropic Claude API and parses structured responses.

Each response is expected to contain:
    STATUS: FREE | OCCUPIED
    CONFIDENCE: high | medium | low
    DESCRIPTION: one sentence describing what the AI sees

Two public functions:
    check_home_spot(image_bytes)  — analyse the home parking zone
    check_scan_position(image_bytes, label)  — analyse a street-scan position
"""

import base64
import logging
import re
from dataclasses import dataclass

import anthropic

import config

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


@dataclass
class VisionResult:
    status: str        # "FREE" or "OCCUPIED"
    confidence: str    # "high", "medium", or "low"
    description: str   # human-readable sentence


_HOME_PROMPT = """You are analysing a parking space on a UK residential street.
The camera is inside a house pointing through a window — ignore any glass
reflections, window frames, or interior glare artefacts. Focus only on vehicles
that are clearly on the road surface.

The parking zone of interest is the area of the image between these fractional
coordinates (0=left/top, 1=right/bottom):
  x: {x_min:.2f} to {x_max:.2f}
  y: {y_min:.2f} to {y_max:.2f}

Determine whether the parking space in that zone is free or occupied by a
vehicle (car, van, lorry, motorcycle, bicycle — any vehicle).

Respond in this exact format with no extra text:
STATUS: FREE
CONFIDENCE: high
DESCRIPTION: <one sentence describing what you see in the parking zone>

Or if occupied:
STATUS: OCCUPIED
CONFIDENCE: high
DESCRIPTION: <one sentence describing what you see in the parking zone>

Use CONFIDENCE: low if the image is very dark, blurry, or ambiguous.
Use CONFIDENCE: medium if you are fairly sure but not certain."""

_SCAN_PROMPT = """You are analysing a frame from a street-scan sequence on a
UK residential street. The camera is inside a house pointing through a window —
ignore any glass reflections, window frames, or interior glare. Focus only on
vehicles clearly on the road surface.

Current camera position: {label}

Is there at least one free parking space visible anywhere in this frame?
A free space is a gap on the road large enough for a car that has no vehicle
parked in it.

Respond in this exact format with no extra text:
STATUS: FREE
CONFIDENCE: high
DESCRIPTION: <one sentence describing what you see>

Or if no free space is visible:
STATUS: OCCUPIED
CONFIDENCE: high
DESCRIPTION: <one sentence describing what you see>

Use CONFIDENCE: low if the image is very dark, blurry, or ambiguous.
Use CONFIDENCE: medium if you are fairly sure but not certain."""


def _call_claude(image_bytes: bytes, prompt: str) -> VisionResult:
    """Send image + prompt to Claude and parse the structured response."""
    b64 = base64.standard_b64encode(image_bytes).decode()
    message = _get_client().messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=256,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    raw = message.content[0].text.strip()
    return _parse_response(raw)


def _parse_response(raw: str) -> VisionResult:
    """Extract STATUS, CONFIDENCE and DESCRIPTION from Claude's response."""
    status_match = re.search(r"STATUS:\s*(FREE|OCCUPIED)", raw, re.IGNORECASE)
    confidence_match = re.search(r"CONFIDENCE:\s*(high|medium|low)", raw, re.IGNORECASE)
    description_match = re.search(r"DESCRIPTION:\s*(.+)", raw, re.IGNORECASE)

    status = status_match.group(1).upper() if status_match else "OCCUPIED"
    confidence = confidence_match.group(1).lower() if confidence_match else "low"
    description = description_match.group(1).strip() if description_match else raw

    return VisionResult(status=status, confidence=confidence, description=description)


def check_home_spot(image_bytes: bytes) -> VisionResult:
    """Analyse the home parking zone in *image_bytes*."""
    zone = config.PARKING_ZONE
    prompt = _HOME_PROMPT.format(
        x_min=zone[0], y_min=zone[1], x_max=zone[2], y_max=zone[3]
    )
    result = _call_claude(image_bytes, prompt)
    logger.info(
        "Home spot check — %s (%s): %s",
        result.status,
        result.confidence,
        result.description,
    )
    return result


def check_scan_position(image_bytes: bytes, label: str) -> VisionResult:
    """Analyse a street-scan frame captured at *label* position."""
    prompt = _SCAN_PROMPT.format(label=label)
    result = _call_claude(image_bytes, prompt)
    logger.info(
        "Scan position '%s' — %s (%s): %s",
        label,
        result.status,
        result.confidence,
        result.description,
    )
    return result


def is_confident_enough(result: VisionResult) -> bool:
    """Return True if the result meets the minimum confidence threshold."""
    levels = {"low": 0, "medium": 1, "high": 2}
    threshold = levels.get(config.MIN_CONFIDENCE, 1)
    return levels.get(result.confidence, 0) >= threshold
