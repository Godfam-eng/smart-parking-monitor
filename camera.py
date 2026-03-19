"""
camera.py — Tapo C225 camera interface for Smart Parking Monitor.

Handles RTSP streaming via OpenCV and pan/tilt control via pytapo.

IMPORTANT — moveMotor() is relative, not absolute:
    pytapo's moveMotor(pan, tilt) moves the camera BY that many degrees from
    its current position.  To support absolute angle commands we track the
    current position in software and send only the delta to the hardware.
    Before any scan sequence, call calibrate_position() (done automatically
    by connect()) to drive the camera to its physical left end-stop so the
    software position matches reality.
"""

import logging
import threading
import time
import urllib.parse
from typing import List, Optional

import cv2
from pytapo import Tapo

from config import Config

logger = logging.getLogger(__name__)

# Safe pan/tilt range for the Tapo C225
_PAN_MIN: int = -180
_PAN_MAX: int = 180
_TILT_MIN: int = -20
_TILT_MAX: int = 20

# Step size (degrees) used by the incremental calibration sweep.
_CALIBRATION_PAN_STEP: int = -90
# Maximum number of steps; 5 × 90° = 450°, more than the full 360° range.
_CALIBRATION_MAX_STEPS: int = 5
# Seconds to wait between each incremental calibration step.
_CALIBRATION_STEP_SETTLE: float = 1.5
# Seconds to wait after the final step for the camera to fully settle.
_CALIBRATION_SETTLE_TIME: float = 3.0

# Position labels — non-overlapping integer ranges covering -90 to +90.
# Each integer in that range falls into exactly one bucket.
_POSITION_LABELS = [
    (-90, -46, "far left"),
    (-45, -16, "left"),
    (-15, 15, "center"),
    (16, 45, "right"),
    (46, 90, "far right"),
]


def _angle_to_position_name(angle: int) -> str:
    """Convert a numeric pan angle to a human-readable position name."""
    for low, high, label in _POSITION_LABELS:
        if low <= angle <= high:
            return label
    return "far right" if angle > 90 else "far left"


def _is_motor_locked_rotor(exc: Exception) -> bool:
    """Return True if *exc* indicates a MOTOR_LOCKED_ROTOR hardware error.

    Some firmware versions (especially with Third-Party Compatibility enabled)
    raise this error (-64304) when the camera reaches its physical end-stop
    instead of silently clamping the movement.
    """
    msg = str(exc)
    return "MOTOR_LOCKED_ROTOR" in msg or "-64304" in msg


class TapoCamera:
    """Interface to the Tapo C225 pan/tilt camera."""

    def __init__(self, config: Config) -> None:
        """Initialise with configuration. Does not connect yet."""
        self.config = config
        self.tapo: Optional[Tapo] = None
        # Software-tracked position — only valid after calibrate_position()
        self._current_pan: int = 0
        self._current_tilt: int = 0
        # Re-entrant lock: allows scan_street() to call move_to_angle() and
        # grab_frame() internally without deadlocking, while still serialising
        # concurrent access from the monitoring loop, bot, and API threads.
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """
        Establish a connection to the Tapo camera and calibrate position.

        Drives the camera to its physical left end-stop so that all
        subsequent move_to_angle() calls use correct absolute deltas.

        Raises:
            ConnectionError: if the camera cannot be reached.
        """
        with self._lock:
            try:
                api_user = (
                    self.config.TAPO_API_USER
                    or self.config.TAPO_CLOUD_USER
                    or self.config.TAPO_USER
                )
                api_password = (
                    self.config.TAPO_API_PASSWORD
                    or self.config.TAPO_CLOUD_PASSWORD
                    or self.config.TAPO_PASSWORD
                )
                # Warn if TAPO_API_USER is set without a matching password
                if self.config.TAPO_API_USER and not self.config.TAPO_API_PASSWORD:
                    logger.warning(
                        "TAPO_API_USER is set but TAPO_API_PASSWORD is empty — "
                        "password will fall back to TAPO_CLOUD_PASSWORD or TAPO_PASSWORD"
                    )
                elif self.config.TAPO_CLOUD_USER and not self.config.TAPO_CLOUD_PASSWORD:
                    logger.warning(
                        "TAPO_CLOUD_USER is set but TAPO_CLOUD_PASSWORD is empty — "
                        "password will fall back to TAPO_PASSWORD"
                    )

                if self.config.TAPO_API_USER or self.config.TAPO_CLOUD_USER:
                    masked = (api_user[0] + "***") if api_user else "***"
                    logger.info(
                        "Connecting pytapo API with dedicated API/cloud credentials (user: %s)",
                        masked,
                    )
                else:
                    logger.info(
                        "Connecting pytapo API with camera account credentials (TAPO_USER)"
                    )
                self.tapo = Tapo(
                    host=self.config.TAPO_IP,
                    user=api_user,
                    password=api_password,
                )
                logger.info(
                    "Connected to Tapo camera at %s", self.config.TAPO_IP
                )
                self.calibrate_position()
            except ConnectionError:
                raise
            except Exception as exc:
                logger.error("Failed to connect to Tapo camera at %s: %s", self.config.TAPO_IP, exc)
                raise ConnectionError(f"Cannot connect to Tapo camera: {exc}") from exc

    # ------------------------------------------------------------------
    # Position calibration
    # ------------------------------------------------------------------

    def calibrate_position(self) -> None:
        """
        Drive the camera to its physical left end-stop to establish a known
        absolute position for delta-based movement.

        Uses incremental steps of _CALIBRATION_PAN_STEP degrees until the
        camera reaches its physical limit (signalled by MOTOR_LOCKED_ROTOR on
        newer firmware) or _CALIBRATION_MAX_STEPS steps have been taken.
        Sets _current_pan to _PAN_MIN (-180°) so all subsequent move_to_angle()
        calls can compute accurate deltas.

        MOTOR_LOCKED_ROTOR (-64304) is treated as a successful end-stop
        detection rather than an error — some firmware versions (especially
        with Third-Party Compatibility enabled) raise this instead of silently
        clamping the movement.

        Raises:
            RuntimeError: if the camera is not connected.
        """
        with self._lock:
            if self.tapo is None:
                raise RuntimeError("Camera not connected. Call connect() first.")

            logger.info("Calibrating camera position: driving to left end-stop…")
            for step in range(_CALIBRATION_MAX_STEPS):
                try:
                    self.tapo.moveMotor(_CALIBRATION_PAN_STEP, 0)
                    time.sleep(_CALIBRATION_STEP_SETTLE)
                except Exception as exc:
                    if _is_motor_locked_rotor(exc):
                        logger.info(
                            "Camera reached physical end-stop (MOTOR_LOCKED_ROTOR) "
                            "at step %d — calibration complete",
                            step + 1,
                        )
                        break
                    else:
                        logger.error("Camera position calibration failed: %s", exc)
                        raise
            else:
                logger.info(
                    "Camera completed full calibration sweep (%d steps) without "
                    "hitting end-stop",
                    _CALIBRATION_MAX_STEPS,
                )

            # Final settle before declaring position known
            time.sleep(_CALIBRATION_SETTLE_TIME)
            self._current_pan = _PAN_MIN
            self._current_tilt = 0
            logger.info(
                "Camera calibrated: position reset to pan=%d (left end-stop)", _PAN_MIN
            )

    # ------------------------------------------------------------------
    # RTSP helpers
    # ------------------------------------------------------------------

    def get_rtsp_url(self) -> str:
        """Return the full RTSP URL for the camera stream (credentials URL-encoded)."""
        user = urllib.parse.quote(self.config.TAPO_USER, safe="")
        password = urllib.parse.quote(self.config.TAPO_PASSWORD, safe="")
        return (
            f"rtsp://{user}:{password}"
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
        with self._lock:
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
        Move camera to the given absolute pan/tilt angles and wait for stabilisation.

        Internally converts the absolute target into a relative delta and calls
        moveMotor(delta_pan, delta_tilt), then updates the software-tracked position.

        Args:
            pan_angle:  Absolute horizontal angle in degrees (negative = left,
                        positive = right).  Clamped to [_PAN_MIN, _PAN_MAX].
            tilt_angle: Absolute vertical angle in degrees (negative = down,
                        positive = up).  Clamped to [_TILT_MIN, _TILT_MAX].
        """
        with self._lock:
            if self.tapo is None:
                raise RuntimeError("Camera not connected. Call connect() first.")

            # Clamp to safe hardware range
            pan_target = max(_PAN_MIN, min(_PAN_MAX, pan_angle))
            tilt_target = max(_TILT_MIN, min(_TILT_MAX, tilt_angle))

            # Compute relative deltas from the last known position
            pan_delta = pan_target - self._current_pan
            tilt_delta = tilt_target - self._current_tilt

            try:
                logger.info(
                    "Moving camera to pan=%d, tilt=%d (delta: pan=%+d, tilt=%+d)",
                    pan_target, tilt_target, pan_delta, tilt_delta,
                )
                self.tapo.moveMotor(pan_delta, tilt_delta)
            except Exception as exc:
                if _is_motor_locked_rotor(exc):
                    logger.warning(
                        "Motor reached physical limit moving to pan=%d, tilt=%d — "
                        "position may be approximate",
                        pan_target, tilt_target,
                    )
                else:
                    logger.error(
                        "Failed to move camera to pan=%d tilt=%d: %s",
                        pan_target, tilt_target, exc,
                    )
                    raise

            # Update tracked position BEFORE sleep so it's always accurate
            self._current_pan = pan_target
            self._current_tilt = tilt_target
            logger.debug("Waiting %.1f s for camera to stabilise", self.config.SCAN_SETTLE_TIME)
            time.sleep(self.config.SCAN_SETTLE_TIME)

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
        with self._lock:
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
