# =============================================================================
# config.py — Smart Parking Monitor — edit this file with your own values
# =============================================================================
# Copy this file and fill in every value marked with REPLACE_ME.
# Never commit real API keys to version control.
# =============================================================================

# --- Tapo C225 camera ---
TAPO_IP = "192.168.1.100"          # Local IP of your Tapo camera (check router)
TAPO_USER = "admin"                # Camera Account username (not Tapo app login)
TAPO_PASS = "REPLACE_ME"           # Camera Account password

# RTSP stream URL (built from the credentials above at runtime)
# Format: rtsp://<user>:<pass>@<ip>/stream1
# You do not normally need to edit this line.
RTSP_URL = f"rtsp://{TAPO_USER}:{TAPO_PASS}@{TAPO_IP}/stream1"

# --- Anthropic / Claude API ---
ANTHROPIC_API_KEY = "REPLACE_ME"   # sk-ant-XXXXXXXXXXXXXXXXXXXXXXXX
CLAUDE_MODEL = "claude-opus-4-5"   # Vision-capable model

# --- Telegram bot ---
TELEGRAM_BOT_TOKEN = "REPLACE_ME"  # Token from @BotFather
TELEGRAM_CHAT_ID = "REPLACE_ME"    # Your personal chat ID from @userinfobot

# --- Pushover push notifications ---
PUSHOVER_USER_KEY = "REPLACE_ME"   # User Key from pushover.net dashboard
PUSHOVER_API_TOKEN = "REPLACE_ME"  # Application API Token from pushover.net

# --- Parking zone (pixel bounding box in the camera image) ---
# Set these after running calibrate.py to focus analysis on the road surface.
# Format: [x_min, y_min, x_max, y_max] as fractions of image width/height (0–1).
PARKING_ZONE = [0.1, 0.3, 0.9, 0.9]

# --- Pan/tilt scan positions (degrees) ---
# Run calibrate.py once to find the right angles for your street, then update
# this list. The camera returns to HOME_PAN after every scan.
# Pan: negative = left, positive = right. Tilt: negative = up, positive = down.
HOME_PAN = 0
HOME_TILT = 0

SCAN_POSITIONS = [
    {"pan": -60, "tilt": 0, "label": "far left"},
    {"pan": -30, "tilt": 0, "label": "mid left"},
    {"pan":   0, "tilt": 0, "label": "home"},
    {"pan":  30, "tilt": 0, "label": "mid right"},
    {"pan":  60, "tilt": 0, "label": "far right"},
]

# --- Monitoring loop ---
CHECK_INTERVAL_SECONDS = 180       # How often to check (default: 3 minutes)
SETTLE_TIME_SECONDS = 2            # Seconds to wait after moving camera before grab
MIN_CONFIDENCE = "medium"          # Ignore results below this: low / medium / high

# --- Quiet hours (no notifications between these 24-hour times) ---
QUIET_HOUR_START = 23              # 11 pm
QUIET_HOUR_END = 7                 # 7 am

# --- HTTP API server ---
API_HOST = "0.0.0.0"
API_PORT = 8080

# --- SQLite database ---
DB_PATH = "parking_history.db"

# --- Calibration output folder ---
CALIBRATION_DIR = "calibration_photos"
