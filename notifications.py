"""
notifications.py — Outbound messages via Telegram and Pushover.

Respects quiet hours: no notifications are sent between
QUIET_HOUR_START and QUIET_HOUR_END (configured in config.py).

Public helpers:
    send_space_free(description, direction)
    send_scan_result(found, label, description)
    send_telegram(text, image_bytes=None)
    send_pushover(title, message, priority=0)
"""

import io
import logging
from datetime import datetime

import requests

import config

logger = logging.getLogger(__name__)

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
TELEGRAM_BASE = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


def _is_quiet_hours() -> bool:
    """Return True if the current local hour falls within quiet hours."""
    hour = datetime.now().hour
    start = config.QUIET_HOUR_START
    end = config.QUIET_HOUR_END
    if start > end:
        return hour >= start or hour < end
    return start <= hour < end


def send_telegram(text: str, image_bytes: bytes | None = None) -> bool:
    """Send a Telegram message (with optional photo) to the configured chat.

    Returns True on success, False on failure.
    """
    try:
        if image_bytes:
            resp = requests.post(
                f"{TELEGRAM_BASE}/sendPhoto",
                data={"chat_id": config.TELEGRAM_CHAT_ID, "caption": text},
                files={"photo": ("snapshot.jpg", io.BytesIO(image_bytes), "image/jpeg")},
                timeout=15,
            )
        else:
            resp = requests.post(
                f"{TELEGRAM_BASE}/sendMessage",
                json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text},
                timeout=10,
            )
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.error("Telegram send failed: %s", exc)
        return False


def send_pushover(title: str, message: str, priority: int = 0) -> bool:
    """Send a Pushover push notification.

    priority: -2 (quiet) / -1 (low) / 0 (normal) / 1 (high bypass DND)
    Returns True on success, False on failure.
    """
    try:
        resp = requests.post(
            PUSHOVER_URL,
            data={
                "token": config.PUSHOVER_API_TOKEN,
                "user": config.PUSHOVER_USER_KEY,
                "title": title,
                "message": message,
                "priority": priority,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        logger.error("Pushover send failed: %s", exc)
        return False


def send_space_free(description: str, direction: str | None = None) -> None:
    """Notify that a parking space near home is now free.

    direction: optional human-readable direction, e.g. "about two cars to the left"
    """
    if _is_quiet_hours():
        logger.info("Quiet hours — suppressing space-free notification")
        return

    if direction:
        text = f"🟢 Parking free — {direction}.\n{description}"
    else:
        text = f"🟢 Your parking spot is free!\n{description}"

    send_telegram(text)
    send_pushover("Parking free 🟢", text, priority=1)


def send_spot_occupied(description: str) -> None:
    """Notify that the home spot has just become occupied."""
    if _is_quiet_hours():
        logger.info("Quiet hours — suppressing spot-occupied notification")
        return
    text = f"🔴 Your spot is now taken.\n{description}"
    send_telegram(text)
    send_pushover("Spot taken 🔴", text, priority=0)


def send_scan_result(found: bool, label: str | None, description: str) -> str:
    """Build and return a human-readable scan-result sentence (also logs it).

    This is the text spoken by Siri via the /scan API endpoint.
    """
    if found and label:
        msg = f"Your spot is taken, but there is a free space {label}."
    elif found:
        msg = "There is a free space visible on the street."
    else:
        msg = "No free spaces visible on the street right now."
    logger.info("Scan result: %s", msg)
    return msg
