"""
homekit.py — HomeKit Occupancy Sensor integration for Smart Parking Monitor.

Exposes the parking spot as a HomeKit Occupancy Sensor using the HAP-python
library, allowing status to be seen natively in the Apple Home app.
"""

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# HAP-python is an optional dependency — the system works without it.
# We import lazily so that the rest of the app is unaffected when the library
# is not installed.
try:
    from pyhap.accessory import Accessory
    from pyhap.accessory_driver import AccessoryDriver
    from pyhap.const import CATEGORY_SENSOR
    _HAP_AVAILABLE = True
except ImportError:
    _HAP_AVAILABLE = False
    Accessory = object  # fallback base class for type checking

# Module-level singleton — set by start_homekit()
_accessory: Optional["ParkingOccupancySensor"] = None


class ParkingOccupancySensor(Accessory):
    """HomeKit Occupancy Sensor that reflects parking spot status."""

    category = CATEGORY_SENSOR if _HAP_AVAILABLE else 10  # type: ignore[assignment]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Add OccupancySensor service
        serv = self.add_preload_service("OccupancySensor")
        self._occupancy_char = serv.get_characteristic("OccupancyDetected")
        self._status_active_char = serv.get_characteristic("StatusActive")

        # Default to not-detected and active
        self._occupancy_char.set_value(0)
        self._status_active_char.set_value(True)

        self._lock = threading.Lock()
        logger.info("ParkingOccupancySensor initialised")

    def update_status(self, status: str) -> None:
        """
        Update the HomeKit occupancy state from a parking status string.

        Args:
            status: "FREE", "OCCUPIED", or "UNKNOWN".
                    UNKNOWN keeps the previous state unchanged.
        """
        with self._lock:
            if status == "OCCUPIED":
                self._occupancy_char.set_value(1)
                logger.debug("HomeKit: OccupancyDetected = 1 (OCCUPIED)")
            elif status == "FREE":
                self._occupancy_char.set_value(0)
                logger.debug("HomeKit: OccupancyDetected = 0 (FREE)")
            else:
                # UNKNOWN — leave previous state, keep StatusActive True
                logger.debug("HomeKit: status UNKNOWN — keeping previous state")

    def set_active(self, active: bool) -> None:
        """Set the StatusActive characteristic (reflects whether the system is running)."""
        with self._lock:
            self._status_active_char.set_value(active)
            logger.debug("HomeKit: StatusActive = %s", active)


def get_homekit_accessory() -> Optional["ParkingOccupancySensor"]:
    """Return the module-level accessory singleton, or None if not started."""
    return _accessory


def start_homekit(config, state=None) -> None:
    """
    Create and start the HomeKit AccessoryDriver.

    Intended to be called in a daemon thread from main.py.  Blocks until the
    driver is stopped (e.g. on SIGTERM).

    Args:
        config: App configuration (Config dataclass).
        state:  ParkingState instance (optional, not currently used but reserved
                for future initialisation of state from DB).
    """
    global _accessory

    if not _HAP_AVAILABLE:
        logger.error(
            "HAP-python is not installed — HomeKit integration disabled. "
            "Install it with: pip install HAP-python"
        )
        return

    if not getattr(config, "HOMEKIT_ENABLE", False):
        logger.info("HomeKit integration disabled (HOMEKIT_ENABLE=false)")
        return

    port = getattr(config, "HOMEKIT_PORT", 51826)
    pin_code = getattr(config, "HOMEKIT_PIN", "031-45-154").encode()
    persist_file = getattr(config, "HOMEKIT_STATE_FILE", "homekit.state")
    name = getattr(config, "HOMEKIT_NAME", "Parking Monitor")

    logger.info(
        "Starting HomeKit accessory '%s' on port %d (PIN: %s, persist: %s)",
        name, port, pin_code.decode(), persist_file,
    )

    try:
        driver = AccessoryDriver(
            port=port,
            persist_file=persist_file,
            pincode=pin_code,
        )
        _accessory = ParkingOccupancySensor(driver, name)
        driver.add_accessory(accessory=_accessory)
        driver.start()  # blocks until driver.stop() is called
    except Exception as exc:
        logger.error("HomeKit driver error: %s", exc, exc_info=True)
