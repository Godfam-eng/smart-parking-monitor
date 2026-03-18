"""
bot.py — Telegram bot for Smart Parking Monitor.

Provides command handlers and natural language understanding for interacting
with the parking system via Telegram. Compatible with python-telegram-bot v20+.
"""

import asyncio
import functools
import logging
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import Config
from camera import TapoCamera
from vision import ParkingVision
from state import ParkingState
from notifications import NotificationManager

logger = logging.getLogger(__name__)

# Module-level references populated by start_bot()
_config: Optional[Config] = None
_camera: Optional[TapoCamera] = None
_vision: Optional[ParkingVision] = None
_state: Optional[ParkingState] = None
_notifications: Optional[NotificationManager] = None
_calibrator = None  # Optional[AutoCalibrator] — imported lazily to avoid circular refs


# ------------------------------------------------------------------
# Security decorator
# ------------------------------------------------------------------

def _authorised(func):
    """Decorator: silently ignore updates from unauthorised chat IDs."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if update.effective_chat is None:
            return
        if str(update.effective_chat.id) != str(_config.TELEGRAM_CHAT_ID):
            logger.warning(
                "Rejected update from unauthorised chat_id=%s", update.effective_chat.id
            )
            return
        await func(update, context)
    return wrapper


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _confidence_emoji(confidence: str) -> str:
    return {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(confidence, "⚪")


def _status_emoji(status: str) -> str:
    return {"FREE": "🅿️", "OCCUPIED": "🚗", "UNKNOWN": "❓"}.get(status, "❓")


async def _send_status_reply(update: Update) -> None:
    """Shared logic: grab frame, analyse, reply with result."""
    await update.message.reply_text("📸 Capturing frame and analysing…")
    try:
        loop = asyncio.get_running_loop()
        image_bytes = await loop.run_in_executor(None, _camera.grab_frame)
        result = await loop.run_in_executor(None, _vision.check_home_spot, image_bytes)
        status = result.get("status", "UNKNOWN")
        confidence = result.get("confidence", "low")
        description = result.get("description", "")

        caption = (
            f"{_status_emoji(status)} *{status}*\n"
            f"Confidence: {_confidence_emoji(confidence)} {confidence}\n"
            f"{description}"
        )

        await update.message.reply_photo(photo=image_bytes, caption=caption, parse_mode="Markdown")

    except Exception as exc:
        logger.error("Error in status handler: %s", exc)
        await update.message.reply_text(f"⚠️ Error checking status: {exc}")


async def _send_scan_reply(update: Update) -> None:
    """Shared logic: full street scan, analyse, report nearest free space."""
    await update.message.reply_text("🔍 Scanning the street… this may take 30–60 seconds.")
    try:
        loop = asyncio.get_running_loop()
        positions = await loop.run_in_executor(None, _camera.scan_street)
        if not positions:
            await update.message.reply_text("⚠️ No frames captured during scan.")
            return

        free_positions = []
        summary_lines = []

        for pos in positions:
            result = await loop.run_in_executor(
                None, _vision.check_scan_position, pos["image"], pos["position_name"]
            )
            status = result.get("status", "UNKNOWN")
            confidence = result.get("confidence", "low")
            description = result.get("description", "")
            line = (
                f"{_status_emoji(status)} *{pos['position_name'].title()}*: {status} "
                f"({_confidence_emoji(confidence)} {confidence})\n_{description}_"
            )
            summary_lines.append(line)
            if status == "FREE":
                free_positions.append((pos, result))

        summary = "\n\n".join(summary_lines)
        await update.message.reply_text(f"🔍 *Street Scan Results*\n\n{summary}", parse_mode="Markdown")

        if free_positions:
            best_pos, best_result = free_positions[0]
            caption = (
                f"✅ First available free space: *{best_pos['position_name'].title()}*\n"
                f"{best_result.get('description', '')}"
            )
            await update.message.reply_photo(
                photo=best_pos["image"], caption=caption, parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("😔 No free spaces found on the street right now.")

    except Exception as exc:
        logger.error("Error in scan handler: %s", exc)
        await update.message.reply_text(f"⚠️ Error scanning street: {exc}")


async def _send_stats_reply(update: Update) -> None:
    """Shared logic: fetch and format database stats."""
    try:
        loop = asyncio.get_running_loop()
        stats = await loop.run_in_executor(None, _state.get_stats)
        last = stats.get("last_check") or {}

        busiest_str = ", ".join(
            f"{h['hour']:02d}:00" for h in stats.get("busiest_hours", [])
        ) or "—"
        freest_str = ", ".join(
            f"{h['hour']:02d}:00" for h in stats.get("freest_hours", [])
        ) or "—"

        text = (
            "📊 *Parking Statistics*\n\n"
            f"🗓 Days of data: *{stats['days_of_data']}*\n"
            f"📋 Total checks: *{stats['total_checks']}*\n"
            f"🅿️ Free: *{stats['free_percentage']}%*  "
            f"🚗 Occupied: *{stats['occupied_percentage']}%*\n\n"
            f"📈 Checks (last 24h): *{stats['checks_last_24h']}*\n"
            f"🔄 State changes (last 24h): *{stats['state_changes_last_24h']}*\n\n"
            f"🕐 Busiest hours: {busiest_str}\n"
            f"🕐 Freest hours: {freest_str}\n\n"
            f"🕐 Last check: *{last.get('timestamp', 'Never')}*\n"
            f"Status: *{last.get('status', 'Unknown')}*"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    except Exception as exc:
        logger.error("Error fetching stats: %s", exc)
        await update.message.reply_text(f"⚠️ Error fetching stats: {exc}")


# ------------------------------------------------------------------
# Command handlers
# ------------------------------------------------------------------

@_authorised
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — Welcome message."""
    text = (
        "👋 *Welcome to Smart Parking Monitor!*\n\n"
        "I watch your parking space 24/7 using an AI-powered camera and alert you when it's free.\n\n"
        "*Commands:*\n"
        "/status — Check current parking status\n"
        "/scan — Scan the entire street for free spaces\n"
        "/snapshot — Get current camera view (no AI)\n"
        "/stats — View parking statistics\n"
        "/calibrate — Run auto-calibration sweep\n"
        "/positions — Show current calibrated scan positions\n"
        "/help — Show this help message"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


@_authorised
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help — List all commands."""
    await cmd_start(update, context)


@_authorised
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status — Grab frame and report parking status."""
    await _send_status_reply(update)


@_authorised
async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/scan — Full street scan."""
    await _send_scan_reply(update)


@_authorised
async def cmd_snapshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/snapshot — Send current camera frame without AI analysis."""
    await update.message.reply_text("📸 Grabbing snapshot…")
    try:
        loop = asyncio.get_running_loop()
        image_bytes = await loop.run_in_executor(None, _camera.get_snapshot)
        await update.message.reply_photo(
            photo=image_bytes, caption="📷 Current camera view"
        )
    except Exception as exc:
        logger.error("Error in snapshot handler: %s", exc)
        await update.message.reply_text(f"⚠️ Error grabbing snapshot: {exc}")


@_authorised
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stats — Parking statistics from database."""
    await _send_stats_reply(update)


@_authorised
async def cmd_calibrate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/calibrate — Trigger auto-calibration sweep."""
    if _calibrator is None:
        await update.message.reply_text(
            "⚠️ Calibration not available — camera or AI not initialised."
        )
        return

    await update.message.reply_text(
        "🔧 Starting auto-calibration… this may take 5–10 minutes.\n"
        "Progress updates will appear here."
    )
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _calibrator.run_calibration)
        scan_str = ", ".join(f"{p}°" for p in result.scan_positions)
        await update.message.reply_text(
            f"✅ Calibration complete!\n"
            f"🏠 Home: {result.home_position}°  |  🔍 Scan: {scan_str}"
        )
    except Exception as exc:
        logger.error("Error in /calibrate handler: %s", exc)
        await update.message.reply_text(f"⚠️ Calibration failed: {exc}")


@_authorised
async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/positions — Show current calibrated scan positions."""
    try:
        cal = _state.get_latest_calibration()
        if cal is None:
            await update.message.reply_text(
                "⚙️ No calibration data found.\n"
                "Run /calibrate to auto-configure the camera."
            )
            return

        scan_positions = cal.get("scan_positions", [])
        scan_str = ", ".join(f"{p}°" for p in scan_positions) if scan_positions else "—"
        restriction = cal.get("opposite_restriction", "unknown").replace("_", " ")
        text = (
            "📍 *Current Camera Configuration*\n\n"
            f"🏠 Home position: *{cal['home_position']}°*\n"
            f"🔍 Scan positions: *{scan_str}*\n"
            f"🅿️ Parking side: *{cal.get('parking_side', '—')}*\n"
            f"🟡 Opposite side: *{restriction}*\n"
            f"📅 Last calibrated: *{cal.get('timestamp', 'unknown')}*"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    except Exception as exc:
        logger.error("Error in /positions handler: %s", exc)
        await update.message.reply_text(f"⚠️ Error fetching positions: {exc}")


# ------------------------------------------------------------------
# Natural language handler
# ------------------------------------------------------------------

@_authorised
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle plain text messages with keyword matching."""
    text = update.message.text.lower()

    if any(kw in text for kw in ("free", "space", "available", "parking")):
        await _send_status_reply(update)
    elif any(kw in text for kw in ("show", "camera", "photo", "see", "look")):
        await cmd_snapshot(update, context)
    elif any(kw in text for kw in ("scan", "street", "find", "search", "check")):
        await _send_scan_reply(update)
    elif any(kw in text for kw in ("stats", "statistics", "history", "data")):
        await _send_stats_reply(update)
    elif any(kw in text for kw in ("calibrate", "recalibrate", "calibration")):
        await cmd_calibrate(update, context)
    elif any(kw in text for kw in ("positions", "angles")):
        await cmd_positions(update, context)
    else:
        await update.message.reply_text(
            "🤔 I didn't understand that. Try /help for available commands."
        )


# ------------------------------------------------------------------
# Application factory
# ------------------------------------------------------------------

def _build_application(cfg: Config) -> Application:
    """Create and configure the bot Application."""
    app = Application.builder().token(cfg.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("snapshot", cmd_snapshot))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("calibrate", cmd_calibrate))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return app


# ------------------------------------------------------------------
# Public entry point (called from main.py)
# ------------------------------------------------------------------

def start_bot(
    cfg: Config,
    camera: TapoCamera,
    vision: ParkingVision,
    state: ParkingState,
    notifications: Optional[NotificationManager] = None,
) -> None:
    """
    Initialise module globals and start the Telegram bot polling loop.

    Intended to be called in a daemon thread from main.py.
    Also works standalone: ``python bot.py``
    """
    global _config, _camera, _vision, _state, _notifications, _calibrator
    _config = cfg
    _camera = camera
    _vision = vision
    _state = state
    _notifications = notifications

    # Create the calibrator lazily here to avoid circular imports at module level
    try:
        from auto_calibrate import AutoCalibrator
        _calibrator = AutoCalibrator(camera, vision, state, notifications)
    except Exception as exc:
        logger.warning("Could not initialise AutoCalibrator in bot: %s", exc)
        _calibrator = None

    logger.info("Starting Telegram bot…")
    app = _build_application(cfg)

    # Run the bot's event loop in this thread.
    # stop_signals=None prevents python-telegram-bot from trying to install
    # signal handlers, which only work in the main thread and would raise
    # ValueError: set_wakeup_fd only works in main thread of the main interpreter.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app.run_polling(drop_pending_updates=True, stop_signals=None)


# ------------------------------------------------------------------
# Standalone entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from config import load_config, validate
    from camera import TapoCamera
    from vision import ParkingVision
    from state import ParkingState
    from notifications import NotificationManager

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cfg = load_config()
    if not validate(cfg):
        sys.exit(1)

    cam = TapoCamera(cfg)
    vis = ParkingVision(cfg)
    db = ParkingState(cfg.DB_PATH)
    notifs = NotificationManager(cfg)

    start_bot(cfg, cam, vis, db, notifs)
