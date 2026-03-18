"""
bot.py — Telegram bot for on-demand parking queries.

Handles commands:
    /status   — quick text status of the home spot
    /scan     — full street scan and report
    /snapshot — send a photo from the camera
    /stats    — parking history statistics
    /help     — list commands

Also understands plain English: "is parking free?", "show me the camera", etc.

Usage:
    python bot.py
"""

import asyncio
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

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


async def _is_authorised(update: Update) -> bool:
    """Only respond to messages from the configured chat ID."""
    return str(update.effective_chat.id) == str(config.TELEGRAM_CHAT_ID)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_authorised(update):
        return
    await update.message.reply_text(
        "🅿️ *Smart Parking Monitor*\n\n"
        "/status — quick parking status\n"
        "/scan — full street scan\n"
        "/snapshot — live camera photo\n"
        "/stats — history & statistics\n"
        "/help — this message\n\n"
        "Or just ask in plain English:\n"
        "_\"Is parking free?\"_ / _\"Show me the camera\"_",
        parse_mode="Markdown",
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_authorised(update):
        return
    await update.message.reply_text("⏳ Checking home spot…")
    try:
        image = camera.get_snapshot()
    except RuntimeError as exc:
        await update.message.reply_text(f"❌ Camera error: {exc}")
        return

    result = vision.check_home_spot(image)
    emoji = "🟢" if result.status == "FREE" else "🔴"
    await update.message.reply_text(
        f"{emoji} *{result.status}* (confidence: {result.confidence})\n{result.description}",
        parse_mode="Markdown",
    )


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_authorised(update):
        return
    await update.message.reply_text("🔍 Running full street scan…")
    positions = await camera.scan_street()
    found = False
    label = None
    description = ""
    for pos in positions:
        if pos["image"] is None:
            continue
        result = vision.check_scan_position(pos["image"], pos["label"])
        if result.status == "FREE" and vision.is_confident_enough(result):
            found = True
            label = pos["label"]
            description = result.description
            break

    msg = notifications.send_scan_result(found, label, description)
    await update.message.reply_text(msg)


async def cmd_snapshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_authorised(update):
        return
    await update.message.reply_text("📸 Grabbing snapshot…")
    try:
        image = camera.get_snapshot()
    except RuntimeError as exc:
        await update.message.reply_text(f"❌ Camera error: {exc}")
        return
    await update.message.reply_photo(photo=image, caption="Live camera snapshot")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _is_authorised(update):
        return
    stats = state.get_stats()
    total = stats["total_checks"]
    pct = stats["free_percentage"]
    busiest = stats["busiest_hour"]
    busiest_str = f"{busiest:02d}:00" if busiest is not None else "n/a"
    await update.message.reply_text(
        f"📊 *Parking Statistics*\n\n"
        f"Total checks: {total}\n"
        f"Space free: {pct}% of the time\n"
        f"Busiest hour: {busiest_str}",
        parse_mode="Markdown",
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle plain-English messages."""
    if not await _is_authorised(update):
        return
    text = update.message.text.lower()
    if any(w in text for w in ["free", "space", "park", "spot", "available"]):
        await cmd_status(update, context)
    elif any(w in text for w in ["scan", "street", "search", "look"]):
        await cmd_scan(update, context)
    elif any(w in text for w in ["photo", "camera", "snapshot", "picture", "show"]):
        await cmd_snapshot(update, context)
    elif any(w in text for w in ["stats", "history", "how often", "percent"]):
        await cmd_stats(update, context)
    else:
        await cmd_help(update, context)


def main() -> None:
    state.init_db()
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("snapshot", cmd_snapshot))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info("Telegram bot starting…")
    app.run_polling()


if __name__ == "__main__":
    main()
