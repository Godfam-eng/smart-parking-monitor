# 🅿️ Smart Parking Monitor

![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python)
![Raspberry Pi 5](https://img.shields.io/badge/Hardware-Raspberry%20Pi%205-red?logo=raspberrypi)
![License MIT](https://img.shields.io/badge/License-MIT-green)

> AI-powered parking monitor using Raspberry Pi 5, Tapo C225 camera, and Claude vision — with Siri, Telegram, and push notifications.

---

## Description

Smart Parking Monitor watches your UK terraced street 24/7 through a window-mounted Tapo C225 pan/tilt camera. It uses Anthropic's Claude vision AI to detect whether your parking space is free or occupied, then sends push notifications to your iPhone and Apple Watch via Pushover and Telegram. Ask Siri "Is parking free?" and get an instant spoken answer — even over 4G via Tailscale.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Your iPhone                         │
│  Siri Shortcut ──► Tailscale ──► HTTP API :8080         │
│  Telegram App  ──► Telegram  ──► Bot Handler            │
│  Pushover App  ◄── Pushover  ◄── Notification Manager   │
└─────────────┬───────────────────────────────────────────┘
              │ Tailscale VPN
┌─────────────▼───────────────────────────────────────────┐
│                  Raspberry Pi 5 (16GB)                  │
│                                                         │
│  main.py ──► camera.py ──► Tapo C225 (RTSP)             │
│    │                           │                        │
│    │         vision.py ◄───────┘ (JPEG frame)           │
│    │             │                                      │
│    │        Claude API (Anthropic)                      │
│    │             │ JSON result                          │
│    ├──► state.py (SQLite)                               │
│    └──► notifications.py ──► Pushover API               │
│                          └──► Telegram Bot API          │
└─────────────────────────────────────────────────────────┘
              │ RTSP / pytapo
┌─────────────▼──────────┐
│     Tapo C225 Camera   │
│   (pan/tilt, 2K, PoE)  │
└────────────────────────┘
```

---

## Features

- ✅ **AI vision analysis** — Claude interprets camera frames to detect free/occupied spaces
- ✅ **Smart notifications** — Pushover (iPhone/Watch) + Telegram, respects quiet hours
- ✅ **Siri integration** — "Hey Siri, is parking free?" returns a spoken answer
- ✅ **Telegram bot** — Full command set plus natural language understanding
- ✅ **Full street scan** — Pan camera across the entire street to find the nearest free space
- ✅ **SQLite statistics** — Track parking patterns, busiest hours, free percentage
- ✅ **HTTP REST API** — Full JSON API plus plain-text Siri-compatible responses
- ✅ **Tailscale remote access** — Secure access from anywhere via VPN
- ✅ **systemd service** — Auto-start on boot, automatic restart on failure
- ✅ **Calibration tool** — Visual sweep tool to configure optimal scan angles
- ✅ **Window glass handling** — Prompts instruct Claude to ignore reflections and glare

---

## Hardware Requirements

| Item | Model | Notes |
|------|-------|-------|
| Single board computer | Raspberry Pi 5 (16GB RAM) | 8GB also works |
| Camera | Tapo C225 | Pan/tilt, 2K, RTSP streaming |
| Storage | 64GB+ microSD (A2 class) | Or USB SSD for reliability |
| Network | Ethernet or WiFi | Ethernet recommended |
| Power | Official Pi 5 USB-C PSU | 5V/5A |
| Mount | Suction cup / window mount | Point at street from inside |

---

## Quick Start

**1. Clone the repository:**
```bash
git clone https://github.com/Godfam-eng/smart-parking-monitor.git
cd smart-parking-monitor
```

**2. Create a virtual environment and install dependencies:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**3. Configure:**
```bash
cp .env.example .env
nano .env  # Fill in your API keys, camera IP, Telegram tokens, etc.
```

**4. Calibrate the camera:**
```bash
python calibrate.py
# Review calibration/index.html, then update SCAN_POSITIONS in .env
```

**5. Test it works:**
```bash
python main.py --skip-bot --skip-api
```

**6. Install as a service (auto-start on boot):**
```bash
sudo cp parking-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable parking-monitor
sudo systemctl start parking-monitor
```

See [SETUP_GUIDE.md](SETUP_GUIDE.md) for the full step-by-step setup.

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `TAPO_IP` | — | Camera IP address (required) |
| `TAPO_USER` | — | Camera admin username (required) |
| `TAPO_PASSWORD` | — | Camera admin password (required) |
| `TAPO_RTSP_PORT` | `554` | RTSP port |
| `TAPO_STREAM_PATH` | `stream1` | RTSP stream path |
| `ANTHROPIC_API_KEY` | — | Claude API key (required) |
| `CLAUDE_MODEL` | `claude-sonnet-4-20250514` | Claude model to use |
| `CLAUDE_MAX_TOKENS` | `1024` | Max response tokens |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token (required) |
| `TELEGRAM_CHAT_ID` | — | Your Telegram chat ID (required) |
| `PUSHOVER_USER_KEY` | — | Pushover user key (optional) |
| `PUSHOVER_API_TOKEN` | — | Pushover app token (optional) |
| `CHECK_INTERVAL` | `180` | Seconds between checks |
| `CONFIDENCE_THRESHOLD` | `medium` | Minimum confidence to trigger notification |
| `QUIET_HOURS_START` | `23` | Hour to start quiet period (no Pushover) |
| `QUIET_HOURS_END` | `7` | Hour to end quiet period |
| `SCAN_POSITIONS` | `-60,-30,0,30,60` | Pan angles for street scan |
| `HOME_POSITION` | `0` | Default/home pan angle |
| `SCAN_SETTLE_TIME` | `2.5` | Seconds to wait after pan move |
| `API_HOST` | `0.0.0.0` | API server bind address |
| `API_PORT` | `8080` | API server port |
| `DB_PATH` | `parking_history.db` | SQLite database path |

---

## Usage

### Siri Shortcuts

1. Open the Shortcuts app on iPhone
2. Create a new shortcut → Add Action → "Get Contents of URL"
3. URL: `http://<pi-tailscale-ip>:8080/status`
4. Add "Speak Text" action
5. Assign Siri phrase: "Is parking free?"

See [docs/SIRI_SHORTCUT_GUIDE.md](docs/SIRI_SHORTCUT_GUIDE.md) for full instructions.

### Telegram Bot Commands

| Command | Description |
|---------|-------------|
| `/status` | Check current parking status with AI analysis |
| `/scan` | Scan entire street for free spaces |
| `/snapshot` | Get current camera view (no AI) |
| `/stats` | View parking statistics |
| `/help` | Show help message |

You can also send natural language messages:
- "Is there a space?" → status check
- "Show me the camera" → snapshot
- "Scan the street" → full scan
- "Show me the stats" → statistics

### HTTP API

| Endpoint | Response | Description |
|----------|----------|-------------|
| `GET /status` | Plain text | Siri-friendly status |
| `GET /status/json` | JSON | Full status with confidence |
| `GET /scan` | Plain text | Siri-friendly scan result |
| `GET /scan/json` | JSON | Full scan results array |
| `GET /snapshot` | JPEG | Current camera frame |
| `GET /stats` | JSON | Database statistics |
| `GET /health` | JSON | System health check |

See [docs/API_REFERENCE.md](docs/API_REFERENCE.md) for full documentation.

### Street Scan

When triggered via `/scan` or `GET /scan`, the camera pans through all configured
`SCAN_POSITIONS`. Claude analyses each frame for any visible free spaces. The nearest
free space is reported first.

---

## Cost Estimate

| Service | Tier | Estimated Monthly Cost |
|---------|------|----------------------|
| Anthropic Claude | API (pay per token) | ~£2–5 (180s interval) |
| Pushover | One-time £5 purchase | £0/month |
| Telegram | Free | £0 |
| Tailscale | Free tier (up to 3 devices) | £0 |
| Electricity (Pi 5) | ~5W continuous | ~£1 |
| **Total** | | **~£3–6/month** |

---

## Screenshots / Demo

*(Screenshots will be added once deployed — camera is inside the house pointing at the street.)*

---

## Roadmap

### Phase 2
- [ ] Multiple parking space tracking
- [ ] ANPR (licence plate recognition) for known vehicles
- [ ] Weather-aware notifications ("It's raining — there's a space!")

### Phase 3
- [ ] Web dashboard (Flask/React) for live view and statistics
- [ ] Geofencing: only notify when approaching home
- [ ] iOS widget showing live status

### Phase 4
- [ ] Neighbour mode: track multiple houses
- [ ] Home Assistant integration
- [ ] MQTT support

### Phase 5
- [ ] On-device inference (replaces Claude API, reduces costs)
- [ ] Multi-camera support

---

## License

MIT License — see [LICENSE](LICENSE) for details.
