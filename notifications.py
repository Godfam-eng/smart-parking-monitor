"""
notifications.py — Pushover and Telegram notification manager.

Sends parking status alerts, errors, and scan results via Pushover and Telegram Bot API.
"""

import io
import logging
import re
from datetime import datetime
from typing import Optional

import requests

from config import Config

logger = logging.getLogger(__name__)

_PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
_TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"

# Characters that need escaping in Telegram Markdown (MarkdownV1)
_MARKDOWN_SPECIAL = re.compile(r"([*_`\[])")


def _escape_markdown(text: str) -> str:
    """Escape Telegram Markdown V1 special characters in *text*."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", text)


class NotificationManager:
    """Sends notifications via Pushover (iOS/watchOS) and Telegram."""

    def __init__(self, config: Config) -> None:
        """Initialise with configuration."""
        self.config = config

    # ------------------------------------------------------------------
    # Quiet hours
    # ------------------------------------------------------------------

    def is_quiet_hours(self) -> bool:
        """
        Return True if the current local time falls within quiet hours.

        Handles midnight crossing (e.g. QUIET_HOURS_START=23, QUIET_HOURS_END=7).
        """
        now = datetime.now()
        hour = now.hour
        start = self.config.QUIET_HOURS_START
        end = self.config.QUIET_HOURS_END

        if start < end:
            # Simple range: e.g. 9 → 17
            return start <= hour < end
        else:
            # Crosses midnight: e.g. 23 → 7
            return hour >= start or hour < end

    # ------------------------------------------------------------------
    # Pushover
    # ------------------------------------------------------------------

    def send_pushover(
        self,
        title: str,
        message: str,
        priority: int = 0,
        image: Optional[bytes] = None,
    ) -> bool:
        """
        Send a push notification via Pushover.

        Args:
            title: Notification title.
            message: Notification body.
            priority: Pushover priority (-2 to 2).
            image: Optional JPEG image bytes to attach.

        Returns:
            True on success, False on failure.
        """
        if not self.config.PUSHOVER_USER_KEY or not self.config.PUSHOVER_API_TOKEN:
            logger.debug("Pushover not configured — skipping notification")
            return False

        payload = {
            "token": self.config.PUSHOVER_API_TOKEN,
            "user": self.config.PUSHOVER_USER_KEY,
            "title": title,
            "message": message,
            "priority": priority,
        }

        try:
            if image:
                files = {"attachment": ("snapshot.jpg", io.BytesIO(image), "image/jpeg")}
                response = requests.post(
                    _PUSHOVER_URL, data=payload, files=files, timeout=10
                )
            else:
                response = requests.post(_PUSHOVER_URL, data=payload, timeout=10)

            if response.status_code == 200:
                logger.info("Pushover notification sent: %s", title)
                return True
            else:
                logger.warning(
                    "Pushover returned HTTP %d: %s", response.status_code, response.text
                )
                return False

        except requests.RequestException as exc:
            logger.error("Failed to send Pushover notification: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Telegram
    # ------------------------------------------------------------------

    def _telegram_url(self, method: str) -> str:
        """Build a Telegram Bot API URL."""
        return _TELEGRAM_API_BASE.format(
            token=self.config.TELEGRAM_BOT_TOKEN, method=method
        )

    def send_telegram(
        self,
        message: str,
        image: Optional[bytes] = None,
        chat_id: Optional[str] = None,
    ) -> bool:
        """
        Send a Telegram message, optionally with a photo.

        Args:
            message: Text message (supports Markdown).
            image: Optional JPEG image bytes.
            chat_id: Override chat ID (defaults to config.TELEGRAM_CHAT_ID).

        Returns:
            True on success, False on failure.
        """
        if not self.config.TELEGRAM_BOT_TOKEN or not self.config.TELEGRAM_CHAT_ID:
            logger.debug("Telegram not configured — skipping notification")
            return False

        target_chat = chat_id or self.config.TELEGRAM_CHAT_ID

        try:
            if image:
                files = {"photo": ("snapshot.jpg", io.BytesIO(image), "image/jpeg")}
                payload = {
                    "chat_id": target_chat,
                    "caption": message,
                    "parse_mode": "Markdown",
                }
                response = requests.post(
                    self._telegram_url("sendPhoto"),
                    data=payload,
                    files=files,
                    timeout=15,
                )
            else:
                payload = {
                    "chat_id": target_chat,
                    "text": message,
                    "parse_mode": "Markdown",
                }
                response = requests.post(
                    self._telegram_url("sendMessage"),
                    json=payload,
                    timeout=15,
                )

            if response.status_code == 200:
                logger.info("Telegram message sent to chat %s", target_chat)
                return True
            else:
                logger.warning(
                    "Telegram returned HTTP %d: %s", response.status_code, response.text[:200]
                )
                return False

        except requests.RequestException as exc:
            logger.error("Failed to send Telegram message: %s", exc)
            return False

    # ------------------------------------------------------------------
    # High-level notification helpers
    # ------------------------------------------------------------------

    def notify_space_free(self, description: str, image: Optional[bytes] = None) -> None:
        """Notify that the parking space is now FREE."""
        title = "🅿️ Space is FREE!"
        safe_desc = _escape_markdown(description)
        message = f"🅿️ *Space is FREE!*\n{safe_desc}"

        try:
            if not self.is_quiet_hours():
                self.send_pushover(title=title, message=description, priority=0, image=image)
            else:
                logger.info("Quiet hours active — suppressing Pushover alert")
        except Exception as exc:
            logger.error("notify_space_free Pushover error: %s", exc)

        try:
            self.send_telegram(message=message, image=image)
        except Exception as exc:
            logger.error("notify_space_free Telegram error: %s", exc)

    def notify_space_occupied(self, description: str, image: Optional[bytes] = None) -> None:
        """Notify that the parking space is now OCCUPIED."""
        safe_desc = _escape_markdown(description)
        message = f"🚗 *Space is now occupied*\n{safe_desc}"
        try:
            self.send_telegram(message=message, image=image)
        except Exception as exc:
            logger.error("notify_space_occupied Telegram error: %s", exc)

    def notify_scan_result(self, result_text: str, image: Optional[bytes] = None) -> None:
        """Send street scan results via Telegram."""
        try:
            self.send_telegram(message=result_text, image=image)
        except Exception as exc:
            logger.error("notify_scan_result Telegram error: %s", exc)

    def notify_error(self, error_msg: str) -> None:
        """Send an error notification via Telegram."""
        message = f"⚠️ *Error:* {_escape_markdown(error_msg)}"
        try:
            self.send_telegram(message=message)
        except Exception as exc:
            logger.error("notify_error Telegram error: %s", exc)

    def notify_startup(self) -> None:
        """Send a startup notification via Telegram."""
        message = (
            "🅿️ *Parking Monitor started!*\n"
            "Monitoring your parking space. Use /help for available commands."
        )
        try:
            self.send_telegram(message=message)
        except Exception as exc:
            logger.error("notify_startup Telegram error: %s", exc)
