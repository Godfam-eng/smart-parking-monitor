"""
main.py — Background monitoring loop.

Checks the home parking spot every CHECK_INTERVAL_SECONDS.
Fires a notification when the state changes (occupied → free or free → occupied).
Runs forever as a system service (see parking-monitor.service).

Usage:
    python main.py
"""

import asyncio
import logging
import time

import config
import camera
import vision
import state
import notifications

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def check_once() -> None:
    """Perform a single parking check and act on state changes."""
    try:
        image = camera.get_snapshot()
    except RuntimeError as exc:
        logger.error("Could not get snapshot: %s", exc)
        return

    result = vision.check_home_spot(image)

    if not vision.is_confident_enough(result):
        logger.info(
            "Confidence too low (%s) — skipping notification", result.confidence
        )
        state.record_check(
            result.status, result.confidence, result.description, "home", False
        )
        return

    changed = state.has_state_changed(result.status, "home")
    state.record_check(
        result.status, result.confidence, result.description, "home", changed
    )

    if changed:
        if result.status == "FREE":
            notifications.send_space_free(result.description)
            logger.info("State change: spot is now FREE — notification sent")
        else:
            notifications.send_spot_occupied(result.description)
            logger.info("State change: spot is now OCCUPIED — notification sent")
    else:
        logger.info("No state change (%s)", result.status)


async def main_loop() -> None:
    """Run check_once in an infinite loop with sleep between iterations."""
    state.init_db()
    logger.info(
        "Smart Parking Monitor started — checking every %ds",
        config.CHECK_INTERVAL_SECONDS,
    )
    while True:
        await check_once()
        await asyncio.sleep(config.CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main_loop())
