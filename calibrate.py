"""
calibrate.py — One-time camera calibration tool.

Moves the Tapo C225 through pan angles from -90° to +90° in configurable steps,
captures a JPEG at each position, and saves them to CALIBRATION_DIR.

After running this:
1. Copy the photos to your Mac with:  scp -r pi@parking-pi.local:~/parking_monitor/calibration_photos .
2. Open each photo and note which angle covers which part of the street.
3. Update SCAN_POSITIONS in config.py with the 4-6 most useful angles.

Usage:
    python calibrate.py [--step DEGREES]

    --step DEGREES   Angular step between photos (default: 15)
"""

import argparse
import asyncio
import logging
import os
import time

import config
import camera

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def calibrate(step: int = 15) -> None:
    os.makedirs(config.CALIBRATION_DIR, exist_ok=True)

    pan_range = range(-90, 91, step)
    total = len(pan_range)
    logger.info(
        "Starting calibration: %d positions, step=%d°, saving to '%s'",
        total,
        step,
        config.CALIBRATION_DIR,
    )

    for i, pan in enumerate(pan_range, start=1):
        logger.info("[%d/%d] Moving to pan=%d°…", i, total, pan)
        await camera.move_camera(pan, config.HOME_TILT)
        time.sleep(config.SETTLE_TIME_SECONDS)

        try:
            image = camera.get_snapshot()
        except RuntimeError as exc:
            logger.warning("Could not grab frame at pan=%d: %s", pan, exc)
            continue

        filename = os.path.join(
            config.CALIBRATION_DIR, f"pan_{pan:+04d}.jpg"
        )
        with open(filename, "wb") as fh:
            fh.write(image)
        logger.info("Saved %s", filename)

    logger.info("Returning to home position…")
    await camera.return_to_home()
    logger.info(
        "Calibration complete. %d photos saved to '%s'.",
        total,
        config.CALIBRATION_DIR,
    )
    logger.info(
        "Next step: copy photos to your Mac, identify useful angles, "
        "then update SCAN_POSITIONS in config.py."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calibrate Tapo C225 pan angles for street scanning."
    )
    parser.add_argument(
        "--step",
        type=int,
        default=15,
        help="Angular step between positions in degrees (default: 15)",
    )
    args = parser.parse_args()
    asyncio.run(calibrate(step=args.step))


if __name__ == "__main__":
    main()
