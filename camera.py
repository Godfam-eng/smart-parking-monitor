"""
camera.py — Tapo C225 camera control.

Handles:
- Connecting to the camera RTSP stream
- Grabbing single JPEG frames
- Moving the pan/tilt motor to a preset angle
- Running the full multi-position street scan sequence
- Returning to the home position
"""

import time
import logging
import cv2
import numpy as np
from tapo import ApiClient

import config

logger = logging.getLogger(__name__)


def get_snapshot() -> bytes:
    """Grab a single JPEG frame from the Tapo RTSP stream.

    Returns raw JPEG bytes suitable for passing to the Claude vision API.
    Raises RuntimeError if the stream cannot be read.
    """
    cap = cv2.VideoCapture(config.RTSP_URL)
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        ret, frame = cap.read()
        if not ret or frame is None:
            raise RuntimeError("Failed to read frame from RTSP stream")
        ok, buf = cv2.imencode(".jpg", frame)
        if not ok:
            raise RuntimeError("Failed to encode frame as JPEG")
        return bytes(buf)
    finally:
        cap.release()


async def move_camera(pan: int, tilt: int) -> None:
    """Move the Tapo C225 to the given pan/tilt position (degrees).

    pan:  negative = left,  positive = right
    tilt: negative = up,    positive = down
    """
    client = ApiClient(config.TAPO_USER, config.TAPO_PASS)
    device = await client.c200(config.TAPO_IP)  # C225 uses the C200 camera API class
    await device.move_motor(pan, tilt)
    logger.debug("Camera moved to pan=%d tilt=%d", pan, tilt)


async def return_to_home() -> None:
    """Return the camera to the configured home position and wait to settle."""
    await move_camera(config.HOME_PAN, config.HOME_TILT)
    time.sleep(config.SETTLE_TIME_SECONDS)


async def scan_street() -> list[dict]:
    """Move through all SCAN_POSITIONS and capture a frame at each.

    Returns a list of dicts:
        [{"pan": int, "tilt": int, "label": str, "image": bytes}, ...]
    """
    results = []
    for pos in config.SCAN_POSITIONS:
        logger.info("Moving to %s (pan=%d)", pos["label"], pos["pan"])
        await move_camera(pos["pan"], pos["tilt"])
        time.sleep(config.SETTLE_TIME_SECONDS)
        try:
            image = get_snapshot()
        except RuntimeError as exc:
            logger.warning("Could not grab frame at %s: %s", pos["label"], exc)
            image = None
        results.append({
            "pan": pos["pan"],
            "tilt": pos["tilt"],
            "label": pos["label"],
            "image": image,
        })
    await return_to_home()
    return results
