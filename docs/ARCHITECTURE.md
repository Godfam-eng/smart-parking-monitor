# System Architecture

## Overview

Smart Parking Monitor is a Python application running on a Raspberry Pi 5. It continuously monitors a parking space visible through a window, using AI vision to classify its state, and notifies the user via multiple channels.

---

## Component Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                      main.py                                │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐    │
│  │  Monitoring  │  │  Telegram    │  │  HTTP API      │    │
│  │  Loop Thread │  │  Bot Thread  │  │  Thread        │    │
│  │  (main)      │  │  (bot.py)    │  │  (api.py)      │    │
│  └──────┬───────┘  └──────┬───────┘  └───────┬────────┘    │
│         │                 │                  │             │
│  ┌──────▼───────────────────────────────────▼────────┐     │
│  │              Shared Resources (thread-safe)        │     │
│  │  TapoCamera  │  ParkingVision  │  ParkingState     │     │
│  └──────┬───────────────────┬────────────────────────┘     │
└─────────│───────────────────│────────────────────────────┘ │
          │                   │
  ┌───────▼──────┐    ┌────────▼─────────┐
  │ Tapo C225    │    │  Anthropic        │
  │ RTSP Stream  │    │  Claude Vision    │
  │ pytapo PTZ   │    │  API              │
  └──────────────┘    └──────────────────┘
```

---

## Component Descriptions

### `main.py` — Orchestrator
- Entry point, parses CLI args
- Validates configuration
- Instantiates all components
- Starts Telegram bot in daemon thread
- Starts HTTP API in daemon thread
- Runs the monitoring loop in the main thread
- Handles SIGINT/SIGTERM for graceful shutdown

### `config.py` — Configuration
- Loads `.env` via python-dotenv
- Exposes a `Config` dataclass with all settings
- `validate()` function checks required fields

### `camera.py` — Camera Interface
- `TapoCamera` class
- Connects via pytapo library (`Tapo(host, user, password)`)
- Captures frames via OpenCV RTSP (`cv2.VideoCapture`)
- Controls pan/tilt via `tapo.moveMotor(pan, tilt)`
- `scan_street()` pans through all configured angles

### `vision.py` — AI Analysis
- `ParkingVision` class
- Sends JPEG frames to Claude via Anthropic SDK
- Parses JSON responses (handles markdown fence wrapping)
- Returns structured `{"status", "confidence", "description"}`

### `notifications.py` — Alert Delivery
- `NotificationManager` class
- Pushover: direct HTTP POST to Pushover API
- Telegram: direct HTTP POST to Telegram Bot API
- Respects quiet hours for Pushover
- Never raises exceptions (all errors are logged)

### `state.py` — Persistence
- `ParkingState` class
- SQLite database (standard library)
- Thread-safe writes via `threading.Lock`
- Records every check and every state change
- Provides statistics and hourly breakdowns

### `bot.py` — Telegram Bot
- Uses python-telegram-bot v20+ (async)
- Authorises by chat ID
- Commands: /status, /scan, /snapshot, /stats, /help
- Natural language keyword handler
- Runs in its own asyncio event loop in a thread

### `api.py` — HTTP Server
- Uses aiohttp
- Siri-compatible plain-text endpoints: `/status`, `/scan`
- Full JSON endpoints: `/status/json`, `/scan/json`, `/stats`
- Raw JPEG endpoint: `/snapshot`
- Health check: `/health`
- Runs in its own asyncio event loop in a thread

---

## Data Flows

### Monitoring Loop (every 180 seconds)

```
Camera.grab_frame()
  → JPEG bytes
  → Vision.check_home_spot(image)
  → {status, confidence, description}
  → State.has_state_changed(status)?
    YES → NotificationManager.notify_*()
    BOTH → State.record_state_change()
  → State.record_check()
  → sleep(CHECK_INTERVAL)
```

### On-Demand Query (Telegram /status or GET /status)

```
User request
  → Camera.grab_frame()
  → Vision.check_home_spot(image)
  → Format response
  → Reply with photo + text
```

### Street Scan (Telegram /scan or GET /scan)

```
User request
  → Camera.scan_street()
    → For each angle in SCAN_POSITIONS:
      → Camera.move_to_angle(angle)
      → Camera.grab_frame()
  → Camera.move_to_home()
  → For each frame:
    → Vision.check_scan_position(frame, position_name)
  → Find free positions
  → Report nearest free space
```

---

## Thread Model

```
Main Thread:        Monitoring loop (blocks on sleep/event)
TelegramBot Thread: asyncio event loop, run_polling()
HttpApi Thread:     asyncio event loop, aiohttp web server
```

All three threads share `TapoCamera`, `ParkingVision`, and `ParkingState`.
`ParkingState` uses a `threading.Lock` for all writes.
`TapoCamera` methods are blocking and not thread-safe — concurrent calls from
the bot and API threads while the monitoring loop is running are unlikely in
normal operation but could cause issues under load. Future enhancement: add a
camera lock.

---

## Database Schema

```sql
CREATE TABLE checks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    DEFAULT CURRENT_TIMESTAMP,
    status      TEXT    NOT NULL,       -- FREE | OCCUPIED | UNKNOWN
    confidence  TEXT    NOT NULL,       -- high | medium | low
    description TEXT,
    angle       INTEGER DEFAULT 0       -- pan angle when captured
);

CREATE TABLE state_changes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    DEFAULT CURRENT_TIMESTAMP,
    old_status  TEXT,                   -- NULL for first record
    new_status  TEXT    NOT NULL,
    description TEXT
);
```

---

## Security Considerations

- **Tailscale VPN**: The HTTP API is not authenticated. It relies on Tailscale for network-level security. Never expose port 8080 to the public internet.
- **Telegram bot**: Only processes messages from `TELEGRAM_CHAT_ID`. Other users' messages are silently ignored.
- **Credentials**: All secrets live in `.env` (excluded from git via `.gitignore`). Never commit `.env`.
- **RTSP stream**: The RTSP URL contains the camera password. Only accessible on the local network or via Tailscale.
- **Claude API**: Images are sent to Anthropic's API. Review Anthropic's data retention policies.
