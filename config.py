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
    # Optional: TP-Link cloud account credentials for pytapo API (pan/tilt control).
    # Many C225 firmware versions require cloud credentials for the local API,
    # while RTSP streaming uses Camera Account credentials.
    # If left blank, TAPO_USER / TAPO_PASSWORD are used for both.
    TAPO_CLOUD_USER: str = ""
    TAPO_CLOUD_PASSWORD: str = ""
    # Optional: pytapo API credentials (Third-Party Compatibility mode or cloud).
    # Takes priority over TAPO_CLOUD_USER / TAPO_CLOUD_PASSWORD.
    # Scenario A — Third-Party Compatibility ON: set TAPO_API_USER=admin,
    #   TAPO_API_PASSWORD=<Camera Account password>.
    # Scenario B — Third-Party Compatibility OFF: set to TP-Link cloud email/password.
    # If left blank, falls back to TAPO_CLOUD_USER / TAPO_CLOUD_PASSWORD, then TAPO_USER / TAPO_PASSWORD.
    TAPO_API_USER: str = ""
    TAPO_API_PASSWORD: str = ""

    # --- Anthropic Claude API ---
    ANTHROPIC_API_KEY: str = ""
    CLAUDE_MODEL: str = "claude-sonnet-4-5"
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
    API_KEY: str = ""

    # --- Database ---
    DB_PATH: str = "parking_history.db"

    # --- Geofencing (optional) ---
    HOME_LAT: float = 0.0
    HOME_LON: float = 0.0

    # --- Street Context ---
    STREET_PARKING_SIDE: str = "near"          # "near" = camera side, "far" = opposite side
    OPPOSITE_SIDE_RESTRICTION: str = "double_yellow"  # "none", "single_yellow", "double_yellow", "no_parking"
    VEHICLE_LENGTH_METRES: float = 4.5         # Owner's vehicle length for space-fit assessment
    MIN_SPACE_METRES: float = 5.0              # Minimum gap (metres) to count as a free space

    # --- Auto-Calibration ---
    AUTO_CALIBRATE: bool = True                # Run auto-calibration on first boot
    CALIBRATION_INTERVAL_DAYS: int = 30        # Re-calibrate every N days (0 = never auto-recalibrate)
    CALIBRATION_ANGLES: List[int] = field(
        default_factory=lambda: [-90, -75, -60, -45, -30, -15, 0, 15, 30, 45, 60, 75, 90]
    )
    CALIBRATION_MIN_USEFULNESS: int = 6        # Minimum usefulness score to include in scan positions

    # --- Safe Pan Bounds (auto-derived from calibration or manually set) ---
    # These constrain all camera movement to the angular range where the
    # street is visible through the window.  Leave at defaults (±180) and
    # auto-calibration will narrow them automatically.
    SAFE_PAN_MIN: int = -180   # leftmost useful angle (from calibration)
    SAFE_PAN_MAX: int = 180    # rightmost useful angle (from calibration)

    # --- Public URL (Tailscale Funnel, optional) ---
    # Set to your Tailscale Funnel URL for public HTTPS access without VPN.
    # Example: https://parking-pi.your-tailnet.ts.net
    # Leave blank to use only Tailscale VPN or local network access.
    PUBLIC_URL: str = ""

    # --- Watch Mode ---
    WATCH_CHECK_INTERVAL: int = 60        # Check interval during /watch (seconds)
    LEAVING_CHECK_INTERVAL: int = 90      # Check interval during /leaving (seconds)
    WATCH_TIMEOUT_HOURS: int = 2          # Auto-cancel timeout for /watch
    LEAVING_GRACE_MINUTES: int = 30       # Extra time after ETA expires before auto-cancel
    LEAVING_DEFAULT_MINUTES: int = 30     # Default ETA when /leaving is used without argument
    LEAVING_UPDATE_INTERVAL: int = 600    # Interval (seconds) for proactive /leaving updates

    # --- Background Scan Cache ---
    # Run a full scan every N background checks and cache the result for instant
    # Siri /status responses that include street-wide availability.
    BACKGROUND_SCAN_EVERY: int = 3        # Full scan every N monitoring loop iterations (0 = disabled)
    SCAN_CACHE_MAX_AGE: int = 600         # Maximum cache age in seconds before treating as stale


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


def _safe_int(name: str, raw: str, default: int) -> int:
    """Parse *raw* as int, logging a warning and returning *default* on failure."""
    try:
        return int(raw)
    except (ValueError, TypeError):
        logger.warning("Invalid value for %s='%s' — using default %d", name, raw, default)
        return default


def _safe_float(name: str, raw: str, default: float) -> float:
    """Parse *raw* as float, logging a warning and returning *default* on failure."""
    try:
        return float(raw)
    except (ValueError, TypeError):
        logger.warning("Invalid value for %s='%s' — using default %s", name, raw, default)
        return default


def load_config() -> Config:
    """Create and return a Config instance populated from environment variables."""
    raw_positions = os.getenv("SCAN_POSITIONS", "-60,-30,0,30,60")
    return Config(
        TAPO_IP=os.getenv("TAPO_IP", ""),
        TAPO_USER=os.getenv("TAPO_USER", ""),
        TAPO_PASSWORD=os.getenv("TAPO_PASSWORD", ""),
        TAPO_RTSP_PORT=_safe_int("TAPO_RTSP_PORT", os.getenv("TAPO_RTSP_PORT", "554"), 554),
        TAPO_STREAM_PATH=os.getenv("TAPO_STREAM_PATH", "stream1"),
        TAPO_CLOUD_USER=os.getenv("TAPO_CLOUD_USER", ""),
        TAPO_CLOUD_PASSWORD=os.getenv("TAPO_CLOUD_PASSWORD", ""),
        TAPO_API_USER=os.getenv("TAPO_API_USER", ""),
        TAPO_API_PASSWORD=os.getenv("TAPO_API_PASSWORD", ""),
        ANTHROPIC_API_KEY=os.getenv("ANTHROPIC_API_KEY", ""),
        CLAUDE_MODEL=os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5"),
        CLAUDE_MAX_TOKENS=_safe_int("CLAUDE_MAX_TOKENS", os.getenv("CLAUDE_MAX_TOKENS", "1024"), 1024),
        TELEGRAM_BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        TELEGRAM_CHAT_ID=os.getenv("TELEGRAM_CHAT_ID", ""),
        PUSHOVER_USER_KEY=os.getenv("PUSHOVER_USER_KEY", ""),
        PUSHOVER_API_TOKEN=os.getenv("PUSHOVER_API_TOKEN", ""),
        CHECK_INTERVAL=_safe_int("CHECK_INTERVAL", os.getenv("CHECK_INTERVAL", "180"), 180),
        CONFIDENCE_THRESHOLD=os.getenv("CONFIDENCE_THRESHOLD", "medium"),
        QUIET_HOURS_START=_safe_int("QUIET_HOURS_START", os.getenv("QUIET_HOURS_START", "23"), 23),
        QUIET_HOURS_END=_safe_int("QUIET_HOURS_END", os.getenv("QUIET_HOURS_END", "7"), 7),
        PARKING_ZONE_TOP=_safe_int("PARKING_ZONE_TOP", os.getenv("PARKING_ZONE_TOP", "30"), 30),
        PARKING_ZONE_BOTTOM=_safe_int("PARKING_ZONE_BOTTOM", os.getenv("PARKING_ZONE_BOTTOM", "80"), 80),
        PARKING_ZONE_LEFT=_safe_int("PARKING_ZONE_LEFT", os.getenv("PARKING_ZONE_LEFT", "20"), 20),
        PARKING_ZONE_RIGHT=_safe_int("PARKING_ZONE_RIGHT", os.getenv("PARKING_ZONE_RIGHT", "80"), 80),
        SCAN_POSITIONS=_parse_scan_positions(raw_positions),
        HOME_POSITION=_safe_int("HOME_POSITION", os.getenv("HOME_POSITION", "0"), 0),
        SCAN_SETTLE_TIME=_safe_float("SCAN_SETTLE_TIME", os.getenv("SCAN_SETTLE_TIME", "2.5"), 2.5),
        API_HOST=os.getenv("API_HOST", "0.0.0.0"),
        API_PORT=_safe_int("API_PORT", os.getenv("API_PORT", "8080"), 8080),
        API_KEY=os.getenv("API_KEY", ""),
        DB_PATH=os.getenv("DB_PATH", "parking_history.db"),
        HOME_LAT=_safe_float("HOME_LAT", os.getenv("HOME_LAT", "0.0"), 0.0),
        HOME_LON=_safe_float("HOME_LON", os.getenv("HOME_LON", "0.0"), 0.0),
        STREET_PARKING_SIDE=os.getenv("STREET_PARKING_SIDE", "near"),
        OPPOSITE_SIDE_RESTRICTION=os.getenv("OPPOSITE_SIDE_RESTRICTION", "double_yellow"),
        VEHICLE_LENGTH_METRES=_safe_float("VEHICLE_LENGTH_METRES", os.getenv("VEHICLE_LENGTH_METRES", "4.5"), 4.5),
        MIN_SPACE_METRES=_safe_float("MIN_SPACE_METRES", os.getenv("MIN_SPACE_METRES", "5.0"), 5.0),
        AUTO_CALIBRATE=os.getenv("AUTO_CALIBRATE", "true").lower() in ("true", "1", "yes"),
        CALIBRATION_INTERVAL_DAYS=_safe_int(
            "CALIBRATION_INTERVAL_DAYS", os.getenv("CALIBRATION_INTERVAL_DAYS", "30"), 30
        ),
        CALIBRATION_ANGLES=_parse_scan_positions(
            os.getenv("CALIBRATION_ANGLES", "-90,-75,-60,-45,-30,-15,0,15,30,45,60,75,90")
        ),
        CALIBRATION_MIN_USEFULNESS=_safe_int(
            "CALIBRATION_MIN_USEFULNESS", os.getenv("CALIBRATION_MIN_USEFULNESS", "6"), 6
        ),
        SAFE_PAN_MIN=_safe_int("SAFE_PAN_MIN", os.getenv("SAFE_PAN_MIN", "-180"), -180),
        SAFE_PAN_MAX=_safe_int("SAFE_PAN_MAX", os.getenv("SAFE_PAN_MAX", "180"), 180),
        PUBLIC_URL=os.getenv("PUBLIC_URL", ""),
        WATCH_CHECK_INTERVAL=_safe_int("WATCH_CHECK_INTERVAL", os.getenv("WATCH_CHECK_INTERVAL", "60"), 60),
        LEAVING_CHECK_INTERVAL=_safe_int("LEAVING_CHECK_INTERVAL", os.getenv("LEAVING_CHECK_INTERVAL", "90"), 90),
        WATCH_TIMEOUT_HOURS=_safe_int("WATCH_TIMEOUT_HOURS", os.getenv("WATCH_TIMEOUT_HOURS", "2"), 2),
        LEAVING_GRACE_MINUTES=_safe_int("LEAVING_GRACE_MINUTES", os.getenv("LEAVING_GRACE_MINUTES", "30"), 30),
        LEAVING_DEFAULT_MINUTES=_safe_int("LEAVING_DEFAULT_MINUTES", os.getenv("LEAVING_DEFAULT_MINUTES", "30"), 30),
        LEAVING_UPDATE_INTERVAL=_safe_int("LEAVING_UPDATE_INTERVAL", os.getenv("LEAVING_UPDATE_INTERVAL", "600"), 600),
        BACKGROUND_SCAN_EVERY=_safe_int("BACKGROUND_SCAN_EVERY", os.getenv("BACKGROUND_SCAN_EVERY", "3"), 3),
        SCAN_CACHE_MAX_AGE=_safe_int("SCAN_CACHE_MAX_AGE", os.getenv("SCAN_CACHE_MAX_AGE", "600"), 600),
    )


def validate(config: Config, *, require_telegram: bool = True, require_anthropic: bool = True) -> bool:
    """
    Validate that all required configuration keys are present and numeric values
    are within acceptable ranges.

    Args:
        config: The Config instance to validate.
        require_telegram: When False, TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
            are not required (use when running with --skip-bot).
        require_anthropic: When False, ANTHROPIC_API_KEY is not required (use
            when testing camera connectivity without AI inference).

    Returns True if valid, False otherwise.
    Prints clear error messages for any missing required keys.
    """
    required = {
        "TAPO_IP": config.TAPO_IP,
        "TAPO_USER": config.TAPO_USER,
        "TAPO_PASSWORD": config.TAPO_PASSWORD,
    }

    if require_anthropic:
        required["ANTHROPIC_API_KEY"] = config.ANTHROPIC_API_KEY

    if require_telegram:
        required["TELEGRAM_BOT_TOKEN"] = config.TELEGRAM_BOT_TOKEN
        required["TELEGRAM_CHAT_ID"] = config.TELEGRAM_CHAT_ID

    missing = [key for key, value in required.items() if not value]

    if missing:
        for key in missing:
            logger.error("Missing required configuration: %s (set in .env file)", key)
        logger.error(
            "Configuration validation failed. Copy .env.example to .env and fill in all values."
        )
        return False

    # Numeric range checks
    range_errors = []

    if not 0 <= config.TAPO_RTSP_PORT <= 65535:
        range_errors.append(f"TAPO_RTSP_PORT={config.TAPO_RTSP_PORT} must be 0–65535")
    if not 0 <= config.API_PORT <= 65535:
        range_errors.append(f"API_PORT={config.API_PORT} must be 0–65535")
    if not 0 <= config.QUIET_HOURS_START <= 23:
        range_errors.append(f"QUIET_HOURS_START={config.QUIET_HOURS_START} must be 0–23")
    if not 0 <= config.QUIET_HOURS_END <= 23:
        range_errors.append(f"QUIET_HOURS_END={config.QUIET_HOURS_END} must be 0–23")
    for zone_name, zone_val in (
        ("PARKING_ZONE_TOP", config.PARKING_ZONE_TOP),
        ("PARKING_ZONE_BOTTOM", config.PARKING_ZONE_BOTTOM),
        ("PARKING_ZONE_LEFT", config.PARKING_ZONE_LEFT),
        ("PARKING_ZONE_RIGHT", config.PARKING_ZONE_RIGHT),
    ):
        if not 0 <= zone_val <= 100:
            range_errors.append(f"{zone_name}={zone_val} must be 0–100")

    if range_errors:
        for err in range_errors:
            logger.error("Configuration range error: %s", err)
        return False

    if config.CONFIDENCE_THRESHOLD not in ("low", "medium", "high"):
        logger.error(
            "CONFIDENCE_THRESHOLD='%s' must be 'low', 'medium', or 'high'",
            config.CONFIDENCE_THRESHOLD,
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

    if not config.API_KEY:
        logger.warning("API_KEY not set — HTTP API is unauthenticated (set API_KEY in .env to enable)")

    if not config.TAPO_API_USER and not config.TAPO_CLOUD_USER:
        logger.warning(
            "TAPO_API_USER is not set — using TAPO_USER for pytapo API. "
            "If you get 'Invalid authentication data', enable Third-Party Compatibility "
            "in the Tapo app and set TAPO_API_USER=admin / TAPO_API_PASSWORD=<Camera Account password>, "
            "or set TAPO_API_USER / TAPO_API_PASSWORD to your TP-Link cloud credentials."
        )
    elif config.TAPO_API_USER and not config.TAPO_API_PASSWORD:
        logger.warning(
            "TAPO_API_USER is set but TAPO_API_PASSWORD is empty — "
            "password will fall back to TAPO_CLOUD_PASSWORD or TAPO_PASSWORD."
        )
    elif not config.TAPO_API_USER and config.TAPO_CLOUD_USER and not config.TAPO_CLOUD_PASSWORD:
        logger.warning(
            "TAPO_CLOUD_USER is set but TAPO_CLOUD_PASSWORD is empty — "
            "password will fall back to TAPO_PASSWORD."
        )

    logger.info("Configuration validated successfully.")
    return True


# Module-level singleton — import this from other modules
config = load_config()
