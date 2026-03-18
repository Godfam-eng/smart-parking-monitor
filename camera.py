"""
camera.py — Tapo C225 camera interface for Smart Parking Monitor.

Handles RTSP streaming via OpenCV and pan/tilt control via pytapo.
"""

import logging
import time
from typing import List, Optional

import cv2
from pytapo import Tapo

from config import Config

logger = logging.getLogger(__name__)

# Position labels keyed by rough pan angle range
_POSITION_LABELS = [
    (-90, -45, "far left"),
    (-45, -15, "left"),
    (-15, 15, "center"),
    (15, 45, "right"),
    (45, 90, "far right"),
]


def _angle_to_position_name(angle: int) -> str:
    """Convert a numeric pan angle to a human-readable position name."""
    for low, high, label in _POSITION_LABELS:
        if low <= angle < high:
            return label
    return "far right" if angle >= 45 else "far left"


class TapoCamera:
    """Interface to the Tapo C225 pan/tilt camera."""

    def __init__(self, config: Config) -> None:
        """Initialise with configuration. Does not connect yet."""
        self.config = config
        self.tapo: Optional[Tapo] = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """
        Establish a connection to the Tapo camera.

        Raises:
            ConnectionError: if the camera cannot be reached.
        """
        try:
            self.tapo = Tapo(
                host=self.config.TAPO_IP,
                user=self.config.TAPO_USER,
                password=self.config.TAPO_PASSWORD,
            )
            logger.info(
                "Connected to Tapo camera at %s", self.config.TAPO_IP
            )
        except Exception as exc:
            logger.error("Failed to connect to Tapo camera at %s: %s", self.config.TAPO_IP, exc)
            raise ConnectionError(f"Cannot connect to Tapo camera: {exc}") from exc

    # ------------------------------------------------------------------
    # RTSP helpers
    # ------------------------------------------------------------------

    def get_rtsp_url(self) -> str:
        """Return the full RTSP URL for the camera stream."""
        return (
            f"rtsp://{self.config.TAPO_USER}:{self.config.TAPO_PASSWORD}"
            f"@{self.config.TAPO_IP}:{self.config.TAPO_RTSP_PORT}"
            f"/{self.config.TAPO_STREAM_PATH}"
        )

    def grab_frame(self) -> bytes:
        """
        Capture a single JPEG frame from the RTSP stream.

        Retries up to 3 times with 2-second delays.

        Returns:
            JPEG-encoded image bytes.

        Raises:
            RuntimeError: if all attempts fail.
        """
        rtsp_url = self.get_rtsp_url()
        last_error: Optional[Exception] = None

        for attempt in range(1, 4):
            cap: Optional[cv2.VideoCapture] = None
            try:
                logger.debug("Grabbing frame (attempt %d/3) from %s", attempt, self.config.TAPO_IP)
                cap = cv2.VideoCapture(rtsp_url)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

                if not cap.isOpened():
                    raise RuntimeError("VideoCapture failed to open RTSP stream")

                # Drain the buffer so we get a fresh frame
                for _ in range(5):
                    cap.grab()

                ret, frame = cap.read()
                if not ret or frame is None:
                    raise RuntimeError("cap.read() returned no frame")

                success, buffer = cv2.imencode(".jpg", frame)
                if not success:
                    raise RuntimeError("cv2.imencode failed")

                logger.debug("Frame captured successfully (%d bytes)", len(buffer.tobytes()))
                return buffer.tobytes()

            except Exception as exc:
                last_error = exc
                logger.warning("Frame grab attempt %d failed: %s", attempt, exc)
                if attempt < 3:
                    time.sleep(2)
            finally:
                if cap is not None:
                    cap.release()

        raise RuntimeError(f"Failed to grab frame after 3 attempts: {last_error}") from last_error

    # ------------------------------------------------------------------
    # Pan/tilt control
    # ------------------------------------------------------------------

    def move_to_angle(self, pan_angle: int, tilt_angle: int = 0) -> None:
        """
        Move camera to the given pan/tilt angles and wait for stabilisation.

        Args:
            pan_angle: Horizontal angle in degrees (negative = left, positive = right).
            tilt_angle: Vertical angle in degrees (negative = down, positive = up).
        """
        if self.tapo is None:
            raise RuntimeError("Camera not connected. Call connect() first.")

        try:
            logger.info("Moving camera to pan=%d, tilt=%d", pan_angle, tilt_angle)
            self.tapo.moveMotor(pan_angle, tilt_angle)
            logger.debug("Waiting %.1f s for camera to stabilise", self.config.SCAN_SETTLE_TIME)
            time.sleep(self.config.SCAN_SETTLE_TIME)
        except Exception as exc:
            logger.error("Failed to move camera to pan=%d tilt=%d: %s", pan_angle, tilt_angle, exc)
            raise

    def move_to_home(self) -> None:
        """Return camera to the configured home position."""
        logger.info("Moving camera to home position (pan=%d)", self.config.HOME_POSITION)
        self.move_to_angle(self.config.HOME_POSITION, 0)

    # ------------------------------------------------------------------
    # Full street scan
    # ------------------------------------------------------------------

    def scan_street(self) -> List[dict]:
        """
        Pan through all configured scan positions and capture a frame at each.

        Always returns to the home position, even if an error occurs.

        Returns:
            List of dicts: ``{"angle": int, "image": bytes, "position_name": str}``
        """
        results: List[dict] = []

        try:
            for angle in self.config.SCAN_POSITIONS:
                position_name = _angle_to_position_name(angle)
                logger.info(
                    "Scanning position: %s (angle=%d)", position_name, angle
                )
                try:
                    self.move_to_angle(angle)
                    image_bytes = self.grab_frame()
                    results.append(
                        {
                            "angle": angle,
                            "image": image_bytes,
                            "position_name": position_name,
                        }
                    )
                    logger.debug(
                        "Captured frame at %s (%d bytes)", position_name, len(image_bytes)
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to capture at position %s (angle=%d): %s",
                        position_name,
                        angle,
                        exc,
                    )
                    # Continue scanning other positions

        finally:
            try:
                self.move_to_home()
            except Exception as exc:
                logger.error("Failed to return to home position after scan: %s", exc)

        return results

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def get_snapshot(self) -> bytes:
        """Grab a single frame from the current camera position."""
        return self.grab_frame()
