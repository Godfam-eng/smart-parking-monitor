"""
main.py — Smart Parking Monitor orchestrator.

Starts the monitoring loop, Telegram bot, and HTTP API server.
Entry point: ``python main.py``
"""

import argparse
import logging
import signal
import sys
import threading
import time
from datetime import datetime, timezone

from config import load_config, validate
from camera import TapoCamera
from vision import ParkingVision
from notifications import NotificationManager
from state import ParkingState
from bot import start_bot
from api import start_api

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Graceful shutdown event
# ---------------------------------------------------------------------------
_shutdown_event = threading.Event()


def _handle_signal(signum: int, frame) -> None:
    """Handle SIGINT / SIGTERM by setting the shutdown event."""
    logger.info("Received signal %d — shutting down…", signum)
    _shutdown_event.set()


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ---------------------------------------------------------------------------
# Confidence threshold helper
# ---------------------------------------------------------------------------

_CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


def _meets_threshold(confidence: str, threshold: str) -> bool:
    """Return True if *confidence* is at or above *threshold*."""
    return _CONFIDENCE_ORDER.get(confidence, 0) >= _CONFIDENCE_ORDER.get(threshold, 1)


# ---------------------------------------------------------------------------
# Main monitoring loop
# ---------------------------------------------------------------------------

def _run_monitoring_loop(
    config,
    camera: TapoCamera,
    vision: ParkingVision,
    notifications: NotificationManager,
    state: ParkingState,
) -> None:
    """Run the main parking-check loop until shutdown is requested."""
    logger.info(
        "Monitoring loop started (interval=%ds, threshold=%s)",
        config.CHECK_INTERVAL,
        config.CONFIDENCE_THRESHOLD,
    )

    last_cleanup = datetime.now(timezone.utc)

    while not _shutdown_event.is_set():
        loop_start = time.monotonic()

        try:
            # 1. Grab frame
            image_bytes = camera.grab_frame()

            # 2. Analyse
            result = vision.check_home_spot(image_bytes)
            status = result.get("status", "UNKNOWN")
            confidence = result.get("confidence", "low")
            description = result.get("description", "")

            logger.info(
                "Check: status=%s confidence=%s — %s", status, confidence, description
            )

            # 3. Save previous status BEFORE recording the current check
            previous = state.get_previous_status()

            # 4. Record the current check first
            state.record_check(status, confidence, description, angle=config.HOME_POSITION)

            # 5. Notify if state changed and confidence threshold met.
            #    Skip on the very first run (previous is None) to avoid a spurious alert.
            if status != "UNKNOWN" and _meets_threshold(confidence, config.CONFIDENCE_THRESHOLD):
                if previous is not None and previous != status:
                    logger.info("State change detected: %s → %s", previous, status)
                    if status == "FREE":
                        notifications.notify_space_free(description, image_bytes)
                    elif status == "OCCUPIED":
                        notifications.notify_space_occupied(description, image_bytes)
                    state.record_state_change(previous, status, description)

        except Exception as exc:
            logger.error("Error in monitoring loop iteration: %s", exc, exc_info=True)
            try:
                notifications.notify_error(str(exc))
            except Exception:
                pass

        # Daily cleanup
        if (datetime.now(timezone.utc) - last_cleanup).days >= 1:
            try:
                state.cleanup_old_records(days=90)
                last_cleanup = datetime.now(timezone.utc)
            except Exception as exc:
                logger.warning("Cleanup failed: %s", exc)

        # Sleep for the remainder of the interval
        elapsed = time.monotonic() - loop_start
        sleep_time = max(0.0, config.CHECK_INTERVAL - elapsed)
        logger.debug("Sleeping %.1f s until next check", sleep_time)
        _shutdown_event.wait(timeout=sleep_time)

    logger.info("Monitoring loop exited")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Parse arguments, initialise components, start threads, run loop."""
    parser = argparse.ArgumentParser(description="Smart Parking Monitor")
    parser.add_argument("--skip-bot", action="store_true", help="Disable Telegram bot")
    parser.add_argument("--skip-api", action="store_true", help="Disable HTTP API server")
    args = parser.parse_args()

    # 1. Configuration
    config = load_config()
    if not validate(config):
        logger.error("Configuration invalid — exiting")
        sys.exit(1)

    # 2. Initialise components
    camera = TapoCamera(config)
    vision = ParkingVision(config)
    notifications = NotificationManager(config)
    state = ParkingState(config.DB_PATH)

    # 3. Startup self-test
    logger.info("Running startup self-test…")
    try:
        camera.connect()
        test_frame = camera.grab_frame()
        logger.info("Camera self-test passed (%d bytes captured)", len(test_frame))
    except Exception as exc:
        logger.warning("Camera self-test failed: %s — continuing anyway", exc)

    # 4. Start Telegram bot in a daemon thread
    if not args.skip_bot and config.TELEGRAM_BOT_TOKEN:
        bot_thread = threading.Thread(
            target=start_bot,
            args=(config, camera, vision, state),
            daemon=True,
            name="TelegramBot",
        )
        bot_thread.start()
        logger.info("Telegram bot thread started")
    else:
        logger.info("Telegram bot disabled")

    # 5. Start HTTP API in a daemon thread
    if not args.skip_api:
        api_thread = threading.Thread(
            target=start_api,
            args=(config, camera, vision, state),
            daemon=True,
            name="HttpApi",
        )
        api_thread.start()
        logger.info("HTTP API thread started on port %d", config.API_PORT)
    else:
        logger.info("HTTP API disabled")

    # 6. Send startup notification
    try:
        notifications.notify_startup()
    except Exception as exc:
        logger.warning("Startup notification failed: %s", exc)

    # 7. Run monitoring loop (blocks until shutdown)
    _run_monitoring_loop(config, camera, vision, notifications, state)

    # 8. Cleanup
    logger.info("Shutdown complete")
    state.close()


if __name__ == "__main__":
    main()
