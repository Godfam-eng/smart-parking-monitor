"""
tests/test_homekit.py — Tests for homekit.py (mocked HAP driver)
"""

import sys
from unittest.mock import MagicMock, patch, PropertyMock

# Mock hardware dependencies
sys.modules.setdefault("cv2", MagicMock())
sys.modules.setdefault("pytapo", MagicMock())

import pytest


# ---------------------------------------------------------------------------
# Mock HAP-python before importing homekit
# ---------------------------------------------------------------------------

def _make_mock_hap():
    """Create mock pyhap modules."""
    mock_pyhap = MagicMock()
    mock_accessory = MagicMock()
    mock_driver = MagicMock()
    mock_const = MagicMock()
    mock_const.CATEGORY_SENSOR = 10

    # Make Accessory a proper base class
    class FakeAccessory:
        def __init__(self, driver, name):
            self.driver = driver
            self.display_name = name

        def add_preload_service(self, name):
            return MagicMock()

    mock_pyhap.accessory.Accessory = FakeAccessory
    mock_pyhap.accessory_driver.AccessoryDriver = MagicMock()
    mock_pyhap.const.CATEGORY_SENSOR = 10
    return mock_pyhap, FakeAccessory


class TestParkingOccupancySensor:
    def setup_method(self):
        """Set up mocked pyhap before each test."""
        self.mock_pyhap, FakeAccessory = _make_mock_hap()

        # Patch sys.modules so homekit imports the mock
        self._patches = {
            "pyhap": self.mock_pyhap,
            "pyhap.accessory": self.mock_pyhap.accessory,
            "pyhap.accessory_driver": self.mock_pyhap.accessory_driver,
            "pyhap.const": self.mock_pyhap.const,
        }
        for mod, mock in self._patches.items():
            sys.modules[mod] = mock

        # Force reimport of homekit to pick up mocked pyhap
        if "homekit" in sys.modules:
            del sys.modules["homekit"]

        import homekit as hk_module
        self.hk_module = hk_module

        # Build a minimal sensor
        mock_service = MagicMock()
        mock_char_occupancy = MagicMock()
        mock_char_active = MagicMock()
        mock_service.get_characteristic.side_effect = lambda name: (
            mock_char_occupancy if name == "OccupancyDetected" else mock_char_active
        )

        self.mock_driver = MagicMock()
        mock_instance = FakeAccessory.__new__(FakeAccessory)
        mock_instance.driver = self.mock_driver
        mock_instance.display_name = "Test Parking"
        mock_instance._lock = __import__("threading").Lock()
        mock_instance._occupancy_char = mock_char_occupancy
        mock_instance._status_active_char = mock_char_active
        self.sensor = mock_instance
        # Bind the update_status method from the real class
        self.sensor.update_status = hk_module.ParkingOccupancySensor.update_status.__get__(
            self.sensor, hk_module.ParkingOccupancySensor
        )
        self.sensor.set_active = hk_module.ParkingOccupancySensor.set_active.__get__(
            self.sensor, hk_module.ParkingOccupancySensor
        )
        self.mock_char_occupancy = mock_char_occupancy
        self.mock_char_active = mock_char_active

    def teardown_method(self):
        for mod in self._patches:
            if mod in sys.modules:
                del sys.modules[mod]
        if "homekit" in sys.modules:
            del sys.modules["homekit"]

    def test_update_status_occupied_sets_1(self):
        self.sensor.update_status("OCCUPIED")
        self.mock_char_occupancy.set_value.assert_called_with(1)

    def test_update_status_free_sets_0(self):
        self.sensor.update_status("FREE")
        self.mock_char_occupancy.set_value.assert_called_with(0)

    def test_update_status_unknown_does_not_change(self):
        self.mock_char_occupancy.set_value.reset_mock()
        self.sensor.update_status("UNKNOWN")
        self.mock_char_occupancy.set_value.assert_not_called()

    def test_set_active_true(self):
        self.sensor.set_active(True)
        self.mock_char_active.set_value.assert_called_with(True)

    def test_set_active_false(self):
        self.sensor.set_active(False)
        self.mock_char_active.set_value.assert_called_with(False)


class TestGetHomekitAccessory:
    def setup_method(self):
        # Ensure homekit module state is clean
        self._patches = {}
        mock_pyhap, _ = _make_mock_hap()
        for mod in ("pyhap", "pyhap.accessory", "pyhap.accessory_driver", "pyhap.const"):
            sys.modules[mod] = mock_pyhap
            self._patches[mod] = mock_pyhap
        if "homekit" in sys.modules:
            del sys.modules["homekit"]

    def teardown_method(self):
        for mod in self._patches:
            if mod in sys.modules:
                del sys.modules[mod]
        if "homekit" in sys.modules:
            del sys.modules["homekit"]

    def test_get_homekit_accessory_returns_none_initially(self):
        import homekit
        homekit._accessory = None
        assert homekit.get_homekit_accessory() is None

    def test_get_homekit_accessory_returns_set_value(self):
        import homekit
        mock_acc = MagicMock()
        homekit._accessory = mock_acc
        assert homekit.get_homekit_accessory() is mock_acc
        homekit._accessory = None


class TestStartHomekitDisabled:
    def setup_method(self):
        mock_pyhap, _ = _make_mock_hap()
        for mod in ("pyhap", "pyhap.accessory", "pyhap.accessory_driver", "pyhap.const"):
            sys.modules[mod] = mock_pyhap
        if "homekit" in sys.modules:
            del sys.modules["homekit"]

    def teardown_method(self):
        for mod in ("pyhap", "pyhap.accessory", "pyhap.accessory_driver", "pyhap.const"):
            if mod in sys.modules:
                del sys.modules[mod]
        if "homekit" in sys.modules:
            del sys.modules["homekit"]

    def test_start_homekit_does_nothing_when_disabled(self):
        import homekit
        from config import Config
        cfg = Config()
        cfg.HOMEKIT_ENABLE = False
        homekit._accessory = None
        homekit.start_homekit(cfg)
        # Should not start the driver
        assert homekit._accessory is None
