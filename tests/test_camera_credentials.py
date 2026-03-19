"""
tests/test_camera_credentials.py — Tests for credential priority in camera.py.

These tests mock cv2 and pytapo so they can run without the C library dependencies
installed.  They verify that TapoCamera.connect() passes the correct credentials to
the pytapo Tapo constructor according to the documented fallback chain:
    TAPO_API_USER → TAPO_CLOUD_USER → TAPO_USER  (and same for password).
"""

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub out cv2 and pytapo so camera.py can be imported without the real libs
# ---------------------------------------------------------------------------

_cv2_stub = types.ModuleType("cv2")
_cv2_stub.VideoCapture = MagicMock()
_cv2_stub.CAP_PROP_BUFFERSIZE = 38
sys.modules.setdefault("cv2", _cv2_stub)

_pytapo_stub = types.ModuleType("pytapo")
_MockTapo = MagicMock(name="Tapo")
_pytapo_stub.Tapo = _MockTapo
sys.modules.setdefault("pytapo", _pytapo_stub)

# Now we can safely import camera
from config import Config  # noqa: E402
from camera import TapoCamera  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(**kwargs) -> Config:
    """Return a minimal Config with only the Tapo-related fields set."""
    defaults = dict(
        TAPO_IP="192.168.1.100",
        TAPO_USER="cam_user",
        TAPO_PASSWORD="cam_pass",
        TAPO_CLOUD_USER="",
        TAPO_CLOUD_PASSWORD="",
        TAPO_API_USER="",
        TAPO_API_PASSWORD="",
    )
    defaults.update(kwargs)
    return Config(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestConnectCredentialPriority:
    """Verify that connect() passes the correct credentials to pytapo.Tapo."""

    def setup_method(self):
        """Reset the Tapo mock before each test."""
        _MockTapo.reset_mock()

    def _connect(self, cfg: Config) -> None:
        """Call connect() with calibrate_position() stubbed out."""
        cam = TapoCamera(cfg)
        with patch.object(cam, "calibrate_position"):
            cam.connect()

    def test_uses_tapo_user_when_no_api_or_cloud(self):
        """Falls back to TAPO_USER/TAPO_PASSWORD when no API or cloud creds set."""
        cfg = _make_cfg()
        self._connect(cfg)
        _MockTapo.assert_called_once_with(
            host="192.168.1.100",
            user="cam_user",
            password="cam_pass",
        )

    def test_uses_cloud_user_when_api_not_set(self):
        """Uses TAPO_CLOUD_USER when TAPO_API_USER is empty."""
        cfg = _make_cfg(TAPO_CLOUD_USER="cloud@example.com", TAPO_CLOUD_PASSWORD="cloud_pass")
        self._connect(cfg)
        _MockTapo.assert_called_once_with(
            host="192.168.1.100",
            user="cloud@example.com",
            password="cloud_pass",
        )

    def test_api_user_takes_priority_over_cloud(self):
        """TAPO_API_USER/TAPO_API_PASSWORD take priority over TAPO_CLOUD_* creds."""
        cfg = _make_cfg(
            TAPO_API_USER="admin",
            TAPO_API_PASSWORD="api_pass",
            TAPO_CLOUD_USER="cloud@example.com",
            TAPO_CLOUD_PASSWORD="cloud_pass",
        )
        self._connect(cfg)
        _MockTapo.assert_called_once_with(
            host="192.168.1.100",
            user="admin",
            password="api_pass",
        )

    def test_api_user_takes_priority_over_tapo_user(self):
        """TAPO_API_USER/TAPO_API_PASSWORD take priority over TAPO_USER/TAPO_PASSWORD."""
        cfg = _make_cfg(TAPO_API_USER="admin", TAPO_API_PASSWORD="admin_pass")
        self._connect(cfg)
        _MockTapo.assert_called_once_with(
            host="192.168.1.100",
            user="admin",
            password="admin_pass",
        )

    def test_falls_back_to_tapo_user_when_api_password_missing(self):
        """If TAPO_API_USER is set but TAPO_API_PASSWORD is empty, the user stays as
        TAPO_API_USER while the password falls back through the chain to TAPO_PASSWORD.
        This covers the Third-Party Compatibility case where username is 'admin'
        and the Camera Account password is reused."""
        cfg = _make_cfg(TAPO_API_USER="admin", TAPO_API_PASSWORD="")
        self._connect(cfg)
        _MockTapo.assert_called_once_with(
            host="192.168.1.100",
            user="admin",
            password="cam_pass",
        )

    def test_rtsp_url_always_uses_tapo_user(self):
        """get_rtsp_url() must always use TAPO_USER/TAPO_PASSWORD, not API credentials."""
        cfg = _make_cfg(TAPO_API_USER="admin", TAPO_API_PASSWORD="admin_pass")
        cam = TapoCamera(cfg)
        url = cam.get_rtsp_url()
        assert "cam_user" in url
        assert "cam_pass" in url
        assert "admin" not in url


class TestSetSafePanBounds:
    """Verify that set_safe_pan_bounds() correctly updates and clamps the safe range."""

    def test_set_safe_pan_bounds_basic(self):
        cfg = _make_cfg()
        cam = TapoCamera(cfg)
        cam.set_safe_pan_bounds(-60, 60)
        assert cam._safe_pan_min == -60
        assert cam._safe_pan_max == 60

    def test_set_safe_pan_bounds_clamps_to_hardware_limits(self):
        cfg = _make_cfg()
        cam = TapoCamera(cfg)
        cam.set_safe_pan_bounds(-999, 999)
        assert cam._safe_pan_min == -180
        assert cam._safe_pan_max == 180

    def test_set_safe_pan_bounds_swaps_if_inverted(self):
        """If pan_min > pan_max, the values should be swapped."""
        cfg = _make_cfg()
        cam = TapoCamera(cfg)
        cam.set_safe_pan_bounds(60, -60)
        assert cam._safe_pan_min == -60
        assert cam._safe_pan_max == 60

    def test_initialised_from_config(self):
        """Safe bounds are initialised from config on construction."""
        cfg = _make_cfg(SAFE_PAN_MIN=-45, SAFE_PAN_MAX=45)
        cam = TapoCamera(cfg)
        assert cam._safe_pan_min == -45
        assert cam._safe_pan_max == 45


class TestMoveToAngleSafeBounds:
    """Verify that move_to_angle() clamps movement to the safe pan bounds."""

    def _make_connected_cam(self, safe_pan_min=-180, safe_pan_max=180):
        """Return a TapoCamera with a mock tapo connection and given safe bounds."""
        cfg = _make_cfg(SAFE_PAN_MIN=safe_pan_min, SAFE_PAN_MAX=safe_pan_max)
        cam = TapoCamera(cfg)
        cam.tapo = MagicMock()
        cam._current_pan = 0
        cam._current_tilt = 0
        return cam

    def test_move_within_safe_bounds(self):
        cam = self._make_connected_cam(safe_pan_min=-60, safe_pan_max=60)
        cam.move_to_angle(30)
        cam.tapo.moveMotor.assert_called_once_with(30, 0)

    def test_move_clamped_to_safe_max(self):
        cam = self._make_connected_cam(safe_pan_min=-60, safe_pan_max=60)
        cam.move_to_angle(90)  # Exceeds safe_pan_max=60
        # Should clamp to 60; delta from 0 is 60
        cam.tapo.moveMotor.assert_called_once_with(60, 0)

    def test_move_clamped_to_safe_min(self):
        cam = self._make_connected_cam(safe_pan_min=-60, safe_pan_max=60)
        cam.move_to_angle(-90)  # Exceeds safe_pan_min=-60
        # Should clamp to -60; delta from 0 is -60
        cam.tapo.moveMotor.assert_called_once_with(-60, 0)

    def test_zero_delta_optimization_skips_motor_call(self):
        """If already at the target, moveMotor should not be called."""
        cam = self._make_connected_cam()
        cam._current_pan = 30
        cam._current_tilt = 0
        cam.move_to_angle(30, 0)
        cam.tapo.moveMotor.assert_not_called()
