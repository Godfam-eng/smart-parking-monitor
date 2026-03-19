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
    _last_watch_update = time.monotonic()
    _scan_counter = 0

    while not _shutdown_event.is_set():
        loop_start = time.monotonic()

        # Check watch mode at the start of each iteration
        watch = state.get_watch_mode()
        is_watching = watch is not None

        try:
            # 1. Ensure camera is at home position before grabbing frame
            camera.move_to_home()

            # 2. Grab frame
            image_bytes = camera.grab_frame()

            # 3. Analyse
            result = vision.check_home_spot(image_bytes)
            status = result.get("status", "UNKNOWN")
            confidence = result.get("confidence", "low")
            description = result.get("description", "")

            logger.info(
                "Check: status=%s confidence=%s — %s", status, confidence, description
            )

            # 4. Save previous status BEFORE recording the current check
            previous = state.get_previous_status()

            # 5. Record the current check first
            state.record_check(status, confidence, description, angle=config.HOME_POSITION)

            # 6. Notify if state changed and confidence threshold met.
            #    Skip on the very first run (previous is None) to avoid a spurious alert.
            if status != "UNKNOWN" and _meets_threshold(confidence, config.CONFIDENCE_THRESHOLD):
                if previous is not None and previous != status:
                    logger.info("State change detected: %s → %s", previous, status)
                    if status == "FREE":
                        notifications.notify_space_free(description, image_bytes)
                    elif status == "OCCUPIED":
                        notifications.notify_space_occupied(description, image_bytes)
                    state.record_state_change(previous, status, description)

            # 7. Watch mode: proactive updates for /leaving mode
            if is_watching and watch["mode"] == "leaving":
                now = time.monotonic()
                if now - _last_watch_update >= config.LEAVING_UPDATE_INTERVAL:
                    _last_watch_update = now
                    if status == "FREE":
                        notifications.send_telegram(
                            message=f"🅿️ SPACE FREE! Get here now.\n{description}"
                        )
                    else:
                        expires_at = datetime.fromisoformat(watch["expires_at"])
                        remaining = expires_at - datetime.now(timezone.utc)
                        mins = max(0, int(remaining.total_seconds() / 60))
                        notifications.send_telegram(
                            message=(
                                f"⏱ Still watching — street still looks full.\n"
                                f"{mins} minutes remaining before auto-cancel."
                            )
                        )

            # 8. Periodic background scan to populate the scan cache.
            #    Uses early-exit iteration: stops as soon as a free space is found.
            _scan_counter += 1
            if config.BACKGROUND_SCAN_EVERY > 0 and _scan_counter >= config.BACKGROUND_SCAN_EVERY:
                _scan_counter = 0
                try:
                    logger.info("Running background scan for cache…")
                    scan_results = []
                    for pos in camera.scan_street_iter():
                        vis_result = vision.check_scan_position(pos["image"], pos["position_name"])
                        entry = {
                            "position_name": pos["position_name"],
                            "angle": pos["angle"],
                            "status": vis_result.get("status", "UNKNOWN"),
                            "confidence": vis_result.get("confidence", "low"),
                            "description": vis_result.get("description", ""),
                        }
                        scan_results.append(entry)
                    # Build summary
                    free_positions = [r for r in scan_results if r["status"] == "FREE"]
                    if free_positions:
                        names = ", ".join(r["position_name"] for r in free_positions)
                        summary = f"Free spaces found: {names}"
                    else:
                        summary = "No free spaces found on the street"
                    state.save_scan_cache(scan_results, summary)
                    logger.info("Background scan cached: %s", summary)
                except Exception as exc:
                    logger.warning("Background scan failed: %s", exc)

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

        # Determine sleep time based on watch mode
        if is_watching:
            if watch["mode"] == "watch":
                check_interval = config.WATCH_CHECK_INTERVAL
            else:
                check_interval = config.LEAVING_CHECK_INTERVAL
        else:
            check_interval = config.CHECK_INTERVAL

        elapsed = time.monotonic() - loop_start
        sleep_time = max(0.0, check_interval - elapsed)
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
    if not validate(config, require_telegram=not args.skip_bot):
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
        logger.error("Camera self-test failed: %s — exiting (systemd will retry)", exc)
        sys.exit(1)

    # 4. Auto-calibration check (runs BEFORE monitoring loop starts so the
    #    camera sweep does not conflict with active monitoring).
    if config.AUTO_CALIBRATE:
        from auto_calibrate import AutoCalibrator
        calibrator = AutoCalibrator(camera, vision, state, notifications)

        if calibrator.needs_calibration():
            # Skip calibration during quiet/nighttime hours — results would be poor.
            if notifications.is_quiet_hours():
                logger.info(
                    "Auto-calibration needed but it's nighttime — deferring until daylight"
                )
            else:
                logger.info("No calibration found — running auto-calibration…")
                try:
                    cal_result = calibrator.run_calibration()
                    config.HOME_POSITION = cal_result.home_position
                    config.SCAN_POSITIONS = cal_result.scan_positions
                    logger.info(
                        "Auto-calibration complete: home=%d, positions=%s",
                        cal_result.home_position,
                        cal_result.scan_positions,
                    )
                except Exception as exc:
                    logger.warning(
                        "Auto-calibration failed: %s — using config defaults", exc
                    )
        else:
            cal_data = calibrator.get_current_calibration()
            if cal_data:
                config.HOME_POSITION = cal_data.home_position
                config.SCAN_POSITIONS = cal_data.scan_positions
                # Apply safe pan bounds from calibration
                safe_min = getattr(cal_data, 'safe_pan_min', config.SAFE_PAN_MIN)
                safe_max = getattr(cal_data, 'safe_pan_max', config.SAFE_PAN_MAX)
                camera.set_safe_pan_bounds(safe_min, safe_max)
                logger.info(
                    "Loaded calibration: home=%d, positions=%s, safe_pan=[%d°, %d°]",
                    cal_data.home_position,
                    cal_data.scan_positions,
                    safe_min,
                    safe_max,
                )

    # 4a. Move camera to home position before monitoring begins.
    try:
        camera.move_to_home()
        logger.info("Camera moved to home position (pan=%d)", config.HOME_POSITION)
    except Exception as exc:
        logger.warning("Failed to move camera to home position: %s", exc)

    # 5. Start Telegram bot in a daemon thread
    if not args.skip_bot and config.TELEGRAM_BOT_TOKEN:
        bot_thread = threading.Thread(
            target=start_bot,
            args=(config, camera, vision, state, notifications),
            daemon=True,
            name="TelegramBot",
        )
        bot_thread.start()
        logger.info("Telegram bot thread started")
    else:
        logger.info("Telegram bot disabled")

    # 6. Start HTTP API in a daemon thread
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
