"""
tests/test_camera_vision_prep.py — Tests for camera.py vision pre-processing methods.

Tests prepare_for_vision() and has_significant_change() on TapoCamera.
These methods run entirely on the CPU (no camera hardware required).
"""

import sys
import importlib
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Dependency setup — must happen BEFORE importing camera
# ---------------------------------------------------------------------------
# Other test files in this suite mock cv2 via sys.modules.setdefault so that
# camera/api modules can be imported without the hardware dependency.  We need
# the REAL cv2 for these tests (they test actual image-processing behaviour),
# so we clear any MagicMock placeholder and ensure cv2 + camera are loaded with
# the real OpenCV.

# 1. Remove any previously mocked cv2 so the real module can be imported.
for _mod in ("cv2", "camera"):
    if _mod in sys.modules and isinstance(sys.modules[_mod], MagicMock):
        del sys.modules[_mod]

# 2. Force-reload camera.py so its module-level `import cv2` uses the real cv2.
#    We first ensure cv2 is importable, then reload camera.
import cv2                          # loads real OpenCV into sys.modules["cv2"]
import numpy as np
if "camera" in sys.modules:
    importlib.reload(sys.modules["camera"])

# 3. Mock pytapo (camera hardware) — the only dep we can't satisfy in CI.
sys.modules.setdefault("pytapo", MagicMock())

import pytest

from config import Config
from camera import TapoCamera


def _make_config(**kwargs) -> Config:
    """Return a minimal Config suitable for vision prep tests."""
    defaults = dict(
        TAPO_IP="192.168.1.1",
        TAPO_USER="admin",
        TAPO_PASSWORD="pass",
        PARKING_ZONE_TOP=20,
        PARKING_ZONE_BOTTOM=80,
        PARKING_ZONE_LEFT=20,
        PARKING_ZONE_RIGHT=80,
        VISION_RESIZE_WIDTH=640,
        VISION_RESIZE_HEIGHT=480,
        VISION_CROP_TO_ZONE=True,
        MOTION_GATE_THRESHOLD=0.02,
    )
    defaults.update(kwargs)
    return Config(**defaults)


def _make_jpeg(width: int = 1280, height: int = 720, color: tuple = (100, 150, 200)) -> bytes:
    """Create a solid-colour JPEG of the given dimensions."""
    frame = np.full((height, width, 3), color, dtype=np.uint8)
    _, buf = cv2.imencode(".jpg", frame)
    return buf.tobytes()


def _make_camera(config: Config) -> TapoCamera:
    """Return a TapoCamera instance that does not attempt to connect."""
    cam = TapoCamera.__new__(TapoCamera)
    cam.config = config
    cam.tapo = None
    cam._current_pan = 0
    cam._current_tilt = 0
    cam._safe_pan_min = config.SAFE_PAN_MIN
    cam._safe_pan_max = config.SAFE_PAN_MAX
    cam._rtsp_stream = None
    import threading
    cam._lock = threading.RLock()
    return cam


# ---------------------------------------------------------------------------
# prepare_for_vision()
# ---------------------------------------------------------------------------

class TestPrepareForVision:
    def test_returns_bytes(self):
        cfg = _make_config()
        cam = _make_camera(cfg)
        raw = _make_jpeg(1280, 720)
        result = cam.prepare_for_vision(raw)
        assert isinstance(result, bytes)

    def test_output_is_valid_jpeg(self):
        cfg = _make_config()
        cam = _make_camera(cfg)
        raw = _make_jpeg(1280, 720)
        result = cam.prepare_for_vision(raw)
        # Valid JPEG starts with the SOI marker
        assert result[:2] == b"\xff\xd8"

    def test_output_smaller_than_input(self):
        """640×480 crop/resize should produce a smaller file than a 1280×720 original."""
        cfg = _make_config(VISION_RESIZE_WIDTH=640, VISION_RESIZE_HEIGHT=480)
        cam = _make_camera(cfg)
        raw = _make_jpeg(1280, 720)
        result = cam.prepare_for_vision(raw)
        assert len(result) < len(raw)

    def test_output_dimensions_match_config(self):
        cfg = _make_config(VISION_RESIZE_WIDTH=320, VISION_RESIZE_HEIGHT=240)
        cam = _make_camera(cfg)
        raw = _make_jpeg(1280, 720)
        result = cam.prepare_for_vision(raw)
        arr = np.frombuffer(result, np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        assert frame is not None
        h, w = frame.shape[:2]
        assert w == 320
        assert h == 240

    def test_crop_disabled(self):
        """With VISION_CROP_TO_ZONE=False, the full frame should be resized."""
        cfg = _make_config(VISION_CROP_TO_ZONE=False, VISION_RESIZE_WIDTH=640, VISION_RESIZE_HEIGHT=480)
        cam = _make_camera(cfg)
        raw = _make_jpeg(1920, 1080)
        result = cam.prepare_for_vision(raw)
        arr = np.frombuffer(result, np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        assert frame is not None
        h, w = frame.shape[:2]
        assert w == 640
        assert h == 480

    def test_crop_enabled(self):
        """With VISION_CROP_TO_ZONE=True, the result should still match target dimensions."""
        cfg = _make_config(VISION_CROP_TO_ZONE=True, VISION_RESIZE_WIDTH=640, VISION_RESIZE_HEIGHT=480)
        cam = _make_camera(cfg)
        raw = _make_jpeg(1920, 1080)
        result = cam.prepare_for_vision(raw)
        arr = np.frombuffer(result, np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        assert frame is not None
        h, w = frame.shape[:2]
        assert w == 640
        assert h == 480

    def test_invalid_input_returns_original(self):
        """Non-decodable bytes should be returned unchanged (graceful fallback)."""
        cfg = _make_config()
        cam = _make_camera(cfg)
        bad_bytes = b"\x00\x01\x02not-a-jpeg"
        result = cam.prepare_for_vision(bad_bytes)
        assert result == bad_bytes

    def test_empty_input_returns_original(self):
        cfg = _make_config()
        cam = _make_camera(cfg)
        result = cam.prepare_for_vision(b"")
        assert result == b""


# ---------------------------------------------------------------------------
# has_significant_change()
# ---------------------------------------------------------------------------

class TestHasSignificantChange:
    def test_identical_frames_return_false(self):
        """Exact same frame should produce zero diff → no change."""
        cfg = _make_config(MOTION_GATE_THRESHOLD=0.02)
        cam = _make_camera(cfg)
        frame = _make_jpeg(640, 480, color=(100, 100, 100))
        result = cam.has_significant_change(frame, frame)
        assert result is False

    def test_very_similar_frames_return_false(self):
        """Frames differing by only a few pixels should not trigger the gate."""
        cfg = _make_config(MOTION_GATE_THRESHOLD=0.02)
        cam = _make_camera(cfg)
        frame_a = _make_jpeg(640, 480, color=(100, 100, 100))
        # Slightly different colour but within 30/255 brightness — below threshold
        frame_b = _make_jpeg(640, 480, color=(105, 105, 105))
        result = cam.has_significant_change(frame_a, frame_b)
        # A 5-level brightness difference is below the >30 pixel-diff threshold
        assert result is False

    def test_very_different_frames_return_true(self):
        """Frames with completely different colours should trigger the gate."""
        cfg = _make_config(MOTION_GATE_THRESHOLD=0.02)
        cam = _make_camera(cfg)
        frame_a = _make_jpeg(640, 480, color=(0, 0, 0))      # black
        frame_b = _make_jpeg(640, 480, color=(255, 255, 255)) # white
        result = cam.has_significant_change(frame_a, frame_b)
        assert result is True

    def test_fails_open_on_invalid_frame_a(self):
        """If frame_a can't be decoded, return True (fail open — call Claude)."""
        cfg = _make_config()
        cam = _make_camera(cfg)
        bad = b"\x00\x01not-a-jpeg"
        good = _make_jpeg(640, 480)
        result = cam.has_significant_change(bad, good)
        assert result is True

    def test_fails_open_on_invalid_frame_b(self):
        """If frame_b can't be decoded, return True (fail open — call Claude)."""
        cfg = _make_config()
        cam = _make_camera(cfg)
        good = _make_jpeg(640, 480)
        bad = b"\x00\x01not-a-jpeg"
        result = cam.has_significant_change(good, bad)
        assert result is True

    def test_fails_open_on_both_invalid(self):
        cfg = _make_config()
        cam = _make_camera(cfg)
        result = cam.has_significant_change(b"bad", b"bad")
        assert result is True

    def test_threshold_override(self):
        """Explicit threshold parameter should override config value."""
        cfg = _make_config(MOTION_GATE_THRESHOLD=0.99)  # very high — would normally return False
        cam = _make_camera(cfg)
        frame_a = _make_jpeg(640, 480, color=(0, 0, 0))
        frame_b = _make_jpeg(640, 480, color=(255, 255, 255))
        # With threshold=0.01, even tiny changes trigger it → should be True for black/white
        result = cam.has_significant_change(frame_a, frame_b, threshold=0.01)
        assert result is True

    def test_high_threshold_suppresses_large_change(self):
        """With threshold=1.0, even completely different frames don't trigger."""
        cfg = _make_config(MOTION_GATE_THRESHOLD=1.0)
        cam = _make_camera(cfg)
        frame_a = _make_jpeg(640, 480, color=(0, 0, 0))
        frame_b = _make_jpeg(640, 480, color=(255, 255, 255))
        result = cam.has_significant_change(frame_a, frame_b)
        assert result is False

    def test_different_size_frames_handled(self):
        """Frames of different dimensions should be resized and compared without error."""
        cfg = _make_config(MOTION_GATE_THRESHOLD=0.02)
        cam = _make_camera(cfg)
        frame_a = _make_jpeg(640, 480, color=(0, 0, 0))
        frame_b = _make_jpeg(320, 240, color=(255, 255, 255))  # different size
        # Should not raise; should return bool
        result = cam.has_significant_change(frame_a, frame_b)
        assert isinstance(result, bool)
