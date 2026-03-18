"""
config.py — Central configuration for Smart Parking Monitor.

Loads all settings from environment variables (via python-dotenv).
Provides a singleton Config instance and a validate() function.
"""

import os
import logging
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class Config:
    """All application configuration, loaded from environment variables."""

    # --- Tapo C225 Camera ---
    TAPO_IP: str = ""
    TAPO_USER: str = ""
    TAPO_PASSWORD: str = ""
    TAPO_RTSP_PORT: int = 554
    TAPO_STREAM_PATH: str = "stream1"

    # --- Anthropic Claude API ---
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-sonnet-4-20250514"
    CLAUDE_MAX_TOKENS: int = 1024

    # --- Telegram Bot ---
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # --- Pushover Notifications ---
    PUSHOVER_USER_KEY: str = ""
    PUSHOVER_API_TOKEN: str = ""

    # --- Monitoring Settings ---
    CHECK_INTERVAL: int = 180
    CONFIDENCE_THRESHOLD: str = "medium"

    # --- Quiet Hours ---
    QUIET_HOURS_START: int = 23
    QUIET_HOURS_END: int = 7

    # --- Parking Zone (percentage of image frame) ---
    PARKING_ZONE_TOP: int = 30
    PARKING_ZONE_BOTTOM: int = 80
    PARKING_ZONE_LEFT: int = 20
    PARKING_ZONE_RIGHT: int = 80

    # --- Street Scan Positions ---
    SCAN_POSITIONS: List[int] = field(default_factory=lambda: [-60, -30, 0, 30, 60])
    HOME_POSITION: int = 0
    SCAN_SETTLE_TIME: float = 2.5

    # --- HTTP API Server ---
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8080

    # --- Database ---
    DB_PATH: str = "parking_history.db"

    # --- Geofencing (optional) ---
    HOME_LAT: float = 0.0
    HOME_LON: float = 0.0


def _parse_scan_positions(raw: str) -> List[int]:
    """Parse a comma-separated string of pan angles into a list of ints."""
    _defaults = [-60, -30, 0, 30, 60]
    if not raw or not raw.strip():
        return _defaults
    try:
        result = [int(x.strip()) for x in raw.split(",") if x.strip()]
        return result if result else _defaults
    except ValueError as exc:
        logger.warning("Could not parse SCAN_POSITIONS '%s': %s — using defaults", raw, exc)
        return _defaults


def load_config() -> Config:
    """Create and return a Config instance populated from environment variables."""
    raw_positions = os.getenv("SCAN_POSITIONS", "-60,-30,0,30,60")
    return Config(
        TAPO_IP=os.getenv("TAPO_IP", ""),
        TAPO_USER=os.getenv("TAPO_USER", ""),
        TAPO_PASSWORD=os.getenv("TAPO_PASSWORD", ""),
        TAPO_RTSP_PORT=int(os.getenv("TAPO_RTSP_PORT", "554")),
        TAPO_STREAM_PATH=os.getenv("TAPO_STREAM_PATH", "stream1"),
        ANTHROPIC_API_KEY=os.getenv("ANTHROPIC_API_KEY", ""),
        CLAUDE_MODEL=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514"),
        CLAUDE_MAX_TOKENS=int(os.getenv("CLAUDE_MAX_TOKENS", "1024")),
        TELEGRAM_BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        TELEGRAM_CHAT_ID=os.getenv("TELEGRAM_CHAT_ID", ""),
        PUSHOVER_USER_KEY=os.getenv("PUSHOVER_USER_KEY", ""),
        PUSHOVER_API_TOKEN=os.getenv("PUSHOVER_API_TOKEN", ""),
        CHECK_INTERVAL=int(os.getenv("CHECK_INTERVAL", "180")),
        CONFIDENCE_THRESHOLD=os.getenv("CONFIDENCE_THRESHOLD", "medium"),
        QUIET_HOURS_START=int(os.getenv("QUIET_HOURS_START", "23")),
        QUIET_HOURS_END=int(os.getenv("QUIET_HOURS_END", "7")),
        PARKING_ZONE_TOP=int(os.getenv("PARKING_ZONE_TOP", "30")),
        PARKING_ZONE_BOTTOM=int(os.getenv("PARKING_ZONE_BOTTOM", "80")),
        PARKING_ZONE_LEFT=int(os.getenv("PARKING_ZONE_LEFT", "20")),
        PARKING_ZONE_RIGHT=int(os.getenv("PARKING_ZONE_RIGHT", "80")),
        SCAN_POSITIONS=_parse_scan_positions(raw_positions),
        HOME_POSITION=int(os.getenv("HOME_POSITION", "0")),
        SCAN_SETTLE_TIME=float(os.getenv("SCAN_SETTLE_TIME", "2.5")),
        API_HOST=os.getenv("API_HOST", "0.0.0.0"),
        API_PORT=int(os.getenv("API_PORT", "8080")),
        DB_PATH=os.getenv("DB_PATH", "parking_history.db"),
        HOME_LAT=float(os.getenv("HOME_LAT", "0.0")),
        HOME_LON=float(os.getenv("HOME_LON", "0.0")),
    )


def validate(config: Config) -> bool:
    """
    Validate that all required configuration keys are present.

    Returns True if valid, False otherwise.
    Prints clear error messages for any missing required keys.
    """
    required = {
        "TAPO_IP": config.TAPO_IP,
        "TAPO_USER": config.TAPO_USER,
        "TAPO_PASSWORD": config.TAPO_PASSWORD,
        "ANTHROPIC_API_KEY": config.ANTHROPIC_API_KEY,
        "TELEGRAM_BOT_TOKEN": config.TELEGRAM_BOT_TOKEN,
        "TELEGRAM_CHAT_ID": config.TELEGRAM_CHAT_ID,
    }

    missing = [key for key, value in required.items() if not value]

    if missing:
        for key in missing:
            logger.error("Missing required configuration: %s (set in .env file)", key)
        logger.error(
            "Configuration validation failed. Copy .env.example to .env and fill in all values."
        )
        return False

    # Warn about optional-but-recommended keys
    recommended = {
        "PUSHOVER_USER_KEY": config.PUSHOVER_USER_KEY,
        "PUSHOVER_API_TOKEN": config.PUSHOVER_API_TOKEN,
    }
    for key, value in recommended.items():
        if not value:
            logger.warning("Recommended configuration not set: %s (Pushover notifications disabled)", key)

    logger.info("Configuration validated successfully.")
    return True


# Module-level singleton — import this from other modules
config = load_config()
