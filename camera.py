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
from typing import Generator, List, Optional

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


def _adaptive_settle(delta_degrees: int, max_settle: float) -> float:
    """Return settle time in seconds proportional to the pan movement distance.

    Uses *max_settle* (from config.SCAN_SETTLE_TIME) as the ceiling so that
    large sweeps still get the full configured delay.  Small adjustments get a
    reduced settle time, saving several seconds per scan.

    Args:
        delta_degrees: Absolute movement distance in degrees.
        max_settle:    Maximum allowed settle time (ceiling).

    Returns:
        Settle time in seconds.
    """
    abs_delta = abs(delta_degrees)
    if abs_delta <= 10:
        return min(0.5, max_settle)
    elif abs_delta <= 30:
        return min(1.0, max_settle)
    elif abs_delta <= 60:
        return min(1.5, max_settle)
    else:
        return max_settle  # full time for large sweeps


# Seconds to wait before attempting to reconnect the RTSP stream after a failure.
_RTSP_RECONNECT_DELAY: float = 1.0


class RTSPStream:
    """Persistent RTSP connection with a 1-frame rolling buffer.

    Keeps a ``cv2.VideoCapture`` open in a background daemon thread and
    continuously reads frames into a single-slot buffer protected by a lock.
    ``get_frame()`` returns the latest buffered frame without reopening the
    connection — eliminating the ~1.5 s TCP/RTSP handshake on every grab.

    The thread reconnects automatically after any failure with a brief pause
    (``_RTSP_RECONNECT_DELAY``) between attempts.
    """

    def __init__(self, rtsp_url: str) -> None:
        self._url = rtsp_url
        self._frame: Optional[bytes] = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the background capture thread."""
        self._running = True
        self._thread = threading.Thread(
            target=self._capture_loop, daemon=True, name="RTSPStream"
        )
        self._thread.start()

    def _capture_loop(self) -> None:
        """Continuously read frames, reconnecting on failure."""
        while self._running:
            cap: Optional[cv2.VideoCapture] = None
            try:
                cap = cv2.VideoCapture(self._url)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                while self._running and cap.isOpened():
                    ret, frame = cap.read()
                    if not ret:
                        break
                    _, buf = cv2.imencode(".jpg", frame)
                    with self._lock:
                        self._frame = buf.tobytes()
            except Exception:
                pass
            finally:
                if cap is not None:
                    cap.release()
            if self._running:
                time.sleep(_RTSP_RECONNECT_DELAY)

    def get_frame(self) -> Optional[bytes]:
        """Return the most recent buffered frame, or None if not yet available."""
        with self._lock:
            return self._frame

    def stop(self) -> None:
        """Stop the background capture thread and wait for it to finish."""
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=5)


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
        # Safe pan bounds — narrowed by calibration to the range where
        # the street is visible through the window.
        self._safe_pan_min: int = config.SAFE_PAN_MIN
        self._safe_pan_max: int = config.SAFE_PAN_MAX
        # Persistent RTSP stream — started by connect(), stopped by disconnect().
        # grab_frame() reads from this buffer first; falls back to per-call
        # VideoCapture when the stream is not available.
        self._rtsp_stream: Optional[RTSPStream] = None

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

                # Start persistent RTSP stream to avoid per-call reconnect overhead.
                rtsp_url = self.get_rtsp_url()
                self._rtsp_stream = RTSPStream(rtsp_url)
                self._rtsp_stream.start()
                logger.info("Persistent RTSP stream started")
            except ConnectionError:
                raise
            except Exception as exc:
                logger.error("Failed to connect to Tapo camera at %s: %s", self.config.TAPO_IP, exc)
                raise ConnectionError(f"Cannot connect to Tapo camera: {exc}") from exc

    # ------------------------------------------------------------------
    # Position calibration
    # ------------------------------------------------------------------

    def set_safe_pan_bounds(self, pan_min: int, pan_max: int) -> None:
        """Update the safe pan range (called after calibration completes).

        Clamps the supplied bounds to the hardware limits and ensures
        pan_min ≤ pan_max (swapping if necessary).
        """
        with self._lock:
            self._safe_pan_min = max(_PAN_MIN, min(pan_min, _PAN_MAX))
            self._safe_pan_max = max(_PAN_MIN, min(pan_max, _PAN_MAX))
            if self._safe_pan_min > self._safe_pan_max:
                self._safe_pan_min, self._safe_pan_max = self._safe_pan_max, self._safe_pan_min
            logger.info(
                "Safe pan bounds updated: [%d°, %d°]",
                self._safe_pan_min, self._safe_pan_max,
            )

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

            # If safe bounds are narrower than the hardware range, move into
            # the safe zone immediately so the camera doesn't linger on
            # blinds, walls, or ceiling.
            if self._safe_pan_min > _PAN_MIN:
                logger.info(
                    "Moving from end-stop (%d°) into safe range (%d°)",
                    _PAN_MIN, self._safe_pan_min,
                )
                delta = self._safe_pan_min - _PAN_MIN
                try:
                    self.tapo.moveMotor(delta, 0)
                    time.sleep(self.config.SCAN_SETTLE_TIME)
                except Exception as exc:
                    if _is_motor_locked_rotor(exc):
                        logger.warning(
                            "MOTOR_LOCKED_ROTOR while moving into safe range — "
                            "position may be approximate"
                        )
                    else:
                        logger.error(
                            "Failed to move into safe range after calibration: %s", exc
                        )
                        raise
                self._current_pan = self._safe_pan_min

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

        Tries the persistent RTSP stream buffer first (fast, no reconnect).
        Falls back to opening a new VideoCapture connection if the stream is
        not running or has no frame buffered yet.

        Retries up to 3 times with 2-second delays on the fallback path.

        Returns:
            JPEG-encoded image bytes.

        Raises:
            RuntimeError: if all attempts fail.
        """
        # Fast path: read from the persistent stream buffer.
        # Intentionally checked outside the camera lock so the RTSPStream's
        # own lock is sufficient and we don't block other callers while waiting.
        if self._rtsp_stream is not None:
            frame = self._rtsp_stream.get_frame()
            if frame is not None:
                logger.debug("Frame served from persistent RTSP buffer (%d bytes)", len(frame))
                return frame

        # Slow path: fall back to a fresh VideoCapture connection.
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

            # Clamp to safe viewing bounds (within hardware limits)
            pan_target = max(self._safe_pan_min, min(self._safe_pan_max, pan_angle))
            tilt_target = max(_TILT_MIN, min(_TILT_MAX, tilt_angle))

            # Skip motor command if we're already at the target position
            if pan_target == self._current_pan and tilt_target == self._current_tilt:
                logger.debug(
                    "Already at pan=%d, tilt=%d — skipping move", pan_target, tilt_target
                )
                return

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
            settle = _adaptive_settle(pan_delta, self.config.SCAN_SETTLE_TIME)
            logger.debug(
                "Waiting %.1f s for camera to stabilise (delta=%d°)", settle, pan_delta
            )
            time.sleep(settle)

    def move_to_home(self) -> None:
        """Return camera to the configured home position."""
        logger.info("Moving camera to home position (pan=%d)", self.config.HOME_POSITION)
        self.move_to_angle(self.config.HOME_POSITION, 0)

    def disconnect(self) -> None:
        """Stop the persistent RTSP stream if running."""
        if self._rtsp_stream is not None:
            self._rtsp_stream.stop()
            self._rtsp_stream = None
            logger.info("Persistent RTSP stream stopped")

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
        return list(self.scan_street_iter())

    def scan_street_iter(self) -> Generator[dict, None, None]:
        """
        Generator that yields one position dict at a time during a street scan.

        The camera always returns to the home position when the generator is
        exhausted or when the caller breaks out early — the ``try/finally``
        block guarantees this even if an exception is raised by the caller.

        Yields:
            Dict with keys ``angle`` (int), ``image`` (bytes), and
            ``position_name`` (str) for each configured scan position.

        Example — early-exit on first free space::

            for pos in camera.scan_street_iter():
                result = vision.check_scan_position(pos["image"], pos["position_name"])
                if result["status"] == "FREE":
                    break  # camera returns home automatically via finally
        """
        with self._lock:
            try:
                for angle in self.config.SCAN_POSITIONS:
                    position_name = _angle_to_position_name(angle)
                    logger.info(
                        "Scanning position: %s (angle=%d)", position_name, angle
                    )
                    try:
                        self.move_to_angle(angle)
                        image_bytes = self.grab_frame()
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
                        continue  # skip this position but keep scanning

                    yield {
                        "angle": angle,
                        "image": image_bytes,
                        "position_name": position_name,
                    }

            finally:
                try:
                    self.move_to_home()
                except Exception as exc:
                    logger.error("Failed to return to home position after scan: %s", exc)

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def get_snapshot(self) -> bytes:
        """Grab a single frame from the current camera position."""
        return self.grab_frame()
