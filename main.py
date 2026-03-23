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
from typing import Optional

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

# Last frame seen by the monitoring loop — used for motion gate comparison.
_last_frame: Optional[bytes] = None


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
    snapshot_history=None,
) -> None:
    """Run the main parking-check loop until shutdown is requested."""
    global _last_frame
    logger.info(
        "Monitoring loop started (interval=%ds, threshold=%s)",
        config.CHECK_INTERVAL,
        config.CONFIDENCE_THRESHOLD,
    )

    last_cleanup = datetime.now(timezone.utc)
    _last_watch_update = time.monotonic()
    _scan_counter = 0

    # Pending notification for debounce/confirmation logic
    # Structure: {"status": str, "previous": str, "description": str,
    #             "timestamp": float, "image_bytes": bytes, "before_image": bytes|None}
    _pending_notification: Optional[dict] = None
    _night_mode_active = False

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

            # 2a. Add frame to snapshot history buffer
            if snapshot_history is not None:
                snapshot_history.add_frame(image_bytes)

            # 3. Motion gate — skip Claude if the parking zone hasn't changed.
            #    Only applies during background monitoring (not watch mode or
            #    on-demand requests).  Fails open: if _last_frame is None or
            #    frames can't be compared, we always proceed to Claude.
            if (
                not is_watching
                and config.MOTION_GATE_ENABLED
                and _last_frame is not None
                and not camera.has_significant_change(_last_frame, image_bytes)
            ):
                logger.debug(
                    "Motion gate: no significant change detected — skipping Claude API call"
                )
                _last_frame = image_bytes
                check_interval = config.CHECK_INTERVAL
                if _night_mode_active:
                    check_interval *= getattr(config, "NIGHT_MODE_INTERVAL_MULTIPLIER", 2)
                elapsed = time.monotonic() - loop_start
                sleep_time = max(0.0, check_interval - elapsed)
                _shutdown_event.wait(timeout=sleep_time)
                continue

            _last_frame = image_bytes

            # 4. Determine model — use night mode model during quiet hours,
            #    otherwise use the fast model for routine background checks.
            is_quiet = notifications.is_quiet_hours()
            if is_quiet and not _night_mode_active:
                logger.info("Night mode activated — switching to %s", config.NIGHT_MODE_MODEL)
                _night_mode_active = True
            elif not is_quiet and _night_mode_active:
                logger.info("Night mode deactivated — returning to fast model")
                _night_mode_active = False

            night_model = getattr(config, "NIGHT_MODE_MODEL", None)
            if is_quiet and night_model:
                model_override = night_model
            else:
                model_override = None

            vision_image = camera.prepare_for_vision(image_bytes)
            result = vision.check_home_spot(
                vision_image,
                use_fast_model=not is_quiet,
                model_override=model_override,
            )
            status = result.get("status", "UNKNOWN")
            confidence = result.get("confidence", "low")
            description = result.get("description", "")

            logger.info(
                "Check: status=%s confidence=%s — %s", status, confidence, description
            )

            # 5. Update HomeKit accessory
            try:
                from homekit import get_homekit_accessory
                hk_acc = get_homekit_accessory()
                if hk_acc is not None:
                    hk_acc.update_status(status)
            except Exception as exc:
                logger.debug("HomeKit update error: %s", exc)

            # 6. Save previous status BEFORE recording the current check
            previous = state.get_previous_status()

            # 7. Record the current check first
            state.record_check(status, confidence, description, angle=config.HOME_POSITION)

            # 8. Notification batching / confirmation logic.
            #    Instead of immediately notifying on state change, store a pending
            #    notification and confirm it on the next cycle.
            confirm_seconds = getattr(config, "NOTIFICATION_CONFIRM_SECONDS", 0)

            if status != "UNKNOWN" and _meets_threshold(confidence, config.CONFIDENCE_THRESHOLD):
                if _pending_notification is not None:
                    pending_status = _pending_notification["status"]
                    if status == pending_status:
                        # Confirmed — send the notification now
                        before_img = _pending_notification.get("before_image")
                        pending_desc = _pending_notification["description"]
                        pending_prev = _pending_notification["previous"]
                        pending_img = _pending_notification["image_bytes"]
                        logger.info(
                            "Confirmed state change: %s → %s — sending notification",
                            pending_prev, pending_status,
                        )
                        if pending_status == "FREE":
                            notifications.notify_space_free(pending_desc, pending_img, before_image=before_img)
                        elif pending_status == "OCCUPIED":
                            notifications.notify_space_occupied(pending_desc, pending_img, before_image=before_img)
                        state.record_state_change(pending_prev, pending_status, pending_desc)
                        if snapshot_history is not None:
                            snapshot_history.save_pair(
                                before_img, pending_img,
                                label=f"{pending_prev}_to_{pending_status}",
                            )
                        _pending_notification = None
                    else:
                        # State flipped back — transient flip, discard
                        logger.info(
                            "Transient flip detected: %s → %s → %s — suppressing notification",
                            _pending_notification["previous"],
                            pending_status,
                            status,
                        )
                        state.record_transient_flip(
                            from_status=_pending_notification["previous"],
                            to_status=pending_status,
                            back_status=status,
                            description=description,
                        )
                        _pending_notification = None

                elif previous is not None and previous != status and confirm_seconds > 0:
                    # State changed — queue a pending notification
                    before_img: Optional[bytes] = None
                    if snapshot_history is not None:
                        before_img, _ = snapshot_history.get_before_after()
                    logger.info(
                        "State change detected: %s → %s — waiting for confirmation",
                        previous, status,
                    )
                    _pending_notification = {
                        "status": status,
                        "previous": previous,
                        "description": description,
                        "timestamp": time.monotonic(),
                        "image_bytes": image_bytes,
                        "before_image": before_img,
                    }

                elif previous is not None and previous != status and confirm_seconds == 0:
                    # Confirmation disabled — notify immediately
                    before_img = None
                    if snapshot_history is not None:
                        before_img, _ = snapshot_history.get_before_after()
                    logger.info("State change detected: %s → %s", previous, status)
                    if status == "FREE":
                        notifications.notify_space_free(description, image_bytes, before_image=before_img)
                    elif status == "OCCUPIED":
                        notifications.notify_space_occupied(description, image_bytes, before_image=before_img)
                    state.record_state_change(previous, status, description)
                    if snapshot_history is not None:
                        snapshot_history.save_pair(
                            before_img, image_bytes, label=f"{previous}_to_{status}"
                        )

            # 9. Watch mode: proactive updates for /leaving mode
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

            # 10. Periodic background scan to populate the scan cache.
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
            # Night mode: double the interval during quiet hours
            if _night_mode_active:
                multiplier = getattr(config, "NIGHT_MODE_INTERVAL_MULTIPLIER", 2)
                check_interval *= multiplier

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

    # Initialise cost tracker if enabled
    cost_tracker = None
    if getattr(config, "COST_TRACKING_ENABLE", True):
        try:
            from cost_tracker import CostTracker
            cost_tracker = CostTracker(config.DB_PATH)
            logger.info("Cost tracking enabled")
        except Exception as exc:
            logger.warning("Could not initialise CostTracker: %s", exc)

    vision = ParkingVision(config, cost_tracker=cost_tracker)
    notifications = NotificationManager(config)
    state = ParkingState(config.DB_PATH)

    # Initialise snapshot history if enabled
    snapshot_history = None
    if getattr(config, "SNAPSHOT_HISTORY_ENABLE", True):
        try:
            from snapshot_history import SnapshotHistory
            snapshot_history = SnapshotHistory(
                snapshot_dir=getattr(config, "SNAPSHOT_DIR", "snapshots"),
                buffer_size=getattr(config, "SNAPSHOT_BUFFER_SIZE", 5),
                max_pairs=getattr(config, "SNAPSHOT_MAX_PAIRS", 100),
                enabled=True,
            )
            logger.info("Snapshot history enabled (dir=%s)", config.SNAPSHOT_DIR)
        except Exception as exc:
            logger.warning("Could not initialise SnapshotHistory: %s", exc)

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
            kwargs={"cost_tracker": cost_tracker},
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
            kwargs={"cost_tracker": cost_tracker},
            daemon=True,
            name="HttpApi",
        )
        api_thread.start()
        logger.info("HTTP API thread started on port %d", config.API_PORT)
    else:
        logger.info("HTTP API disabled")

    # 6a. Start HomeKit in a daemon thread (if enabled)
    if getattr(config, "HOMEKIT_ENABLE", False):
        try:
            from homekit import start_homekit
            homekit_thread = threading.Thread(
                target=start_homekit,
                args=(config, state),
                daemon=True,
                name="HomeKit",
            )
            homekit_thread.start()
            logger.info("HomeKit thread started on port %d", config.HOMEKIT_PORT)
        except Exception as exc:
            logger.warning("Could not start HomeKit thread: %s", exc)

    # 7. Send startup notification
    try:
        notifications.notify_startup()
    except Exception as exc:
        logger.warning("Startup notification failed: %s", exc)

    # 8. Run monitoring loop (blocks until shutdown)
    _run_monitoring_loop(config, camera, vision, notifications, state, snapshot_history=snapshot_history)

    # 9. Cleanup
    logger.info("Shutdown complete")
    state.close()
    if cost_tracker is not None:
        try:
            cost_tracker.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
