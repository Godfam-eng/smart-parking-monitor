# Smart Parking Monitor — Complete Setup Guide

This guide takes you from zero to a fully operational parking monitor running 24/7 on a Raspberry Pi 5 with a Tapo C225 camera.  Follow every step in order — each one is required for a working system.

---

## Pre-Arrival Checklist

Create accounts and gather credentials **before** you sit down at the Pi:

- [ ] **Anthropic** — https://console.anthropic.com → create an API key
- [ ] **Telegram** — Create a bot via [@BotFather](https://t.me/botfather), note the token.  Send a message to your new bot so you can retrieve the chat ID later.
- [ ] **Pushover** — https://pushover.net → copy your User Key, create an Application for the API Token
- [ ] **Tailscale** — https://tailscale.com → free account, install on your iPhone now
- [ ] **Tapo C225 — Camera Account** (most common first-time failure):
  1. Open the **Tapo app** on your phone → tap the C225 camera tile
  2. Tap ⚙️ → **Advanced Settings** → **Camera Account**
  3. Create a dedicated **username** and **password** (write them down)
  4. Also enable **RTSP**: ⚙️ → **Advanced Settings** → **RTSP** → toggle on

  > ⚠️ These credentials are **NOT** your TP-Link cloud login.  The camera account is separate.

---

## Step 1: Flash the SD Card

1. Download [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Choose **Raspberry Pi OS Lite (64-bit)** — no desktop needed
3. Click the **⚙️ gear / Edit Settings** icon and configure:
   - **Hostname**: `parking-pi`
   - **Enable SSH** → "Use password authentication"
   - **Username**: `pi`  ← use exactly this if you want to follow the guide verbatim
   - **Password**: a strong password of your choice
   - **WiFi**: enter your SSID + password (or skip if using Ethernet)
4. Flash, insert into Pi, power on

> If you chose a different username, replace every `pi` path in this guide (and in `parking-monitor.service`) with your actual username.

---

## Step 2: First Boot and SSH

```bash
# From your Mac/PC on the same network:
ssh pi@parking-pi.local

# If hostname doesn't resolve, find the Pi's IP in your router DHCP table:
ssh pi@192.168.1.xxx
```

---

## Step 3: System Updates and Dependencies

```bash
sudo apt update && sudo apt upgrade -y

# Python tools, OpenCV via apt (avoids 30-60 min ARM64 compile from pip),
# and git
sudo apt install -y python3-pip python3-venv python3-opencv git
```

> **Why `python3-opencv` via apt?**  PyPI does not always provide pre-built ARM64 wheels for `opencv-python-headless`.  Compiling from source takes 30–60 minutes and often fails without extra build dependencies.  The apt package is pre-built for ARM64 and installs in seconds.

---

## Step 4: Clone the Repository

```bash
cd ~
git clone https://github.com/Godfam-eng/smart-parking-monitor.git
cd smart-parking-monitor
```

---

## Step 5: Python Environment

```bash
# --system-site-packages lets the venv see the apt-installed python3-opencv
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

> **Do NOT omit `--system-site-packages`.**  Without it the venv cannot see `cv2` and you will get `ModuleNotFoundError: No module named 'cv2'` at runtime.

---

## Step 6: Configure `.env`

```bash
cp .env.example .env
nano .env
```

Fill in every value.  Key fields:

| Key | Where to find it |
|-----|-----------------|
| `TAPO_IP` | Router DHCP table or Tapo app → Device Info |
| `TAPO_USER` | Camera Account username you created in Pre-Arrival step |
| `TAPO_PASSWORD` | Camera Account password you created in Pre-Arrival step |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com |
| `TELEGRAM_BOT_TOKEN` | From BotFather |
| `TELEGRAM_CHAT_ID` | Send `/start` to your bot, then visit:<br>`https://api.telegram.org/bot<TOKEN>/getUpdates` |
| `PUSHOVER_USER_KEY` | https://pushover.net → your User Key |
| `PUSHOVER_API_TOKEN` | Pushover → Your Applications |

Leave `API_KEY` blank if your Pi is behind Tailscale (the default setup).  Set it to a random secret if you ever expose the port to the internet.

---

## Step 6a: Customise Street Context

The AI prompts are pre-configured for a **one-sided parking UK terraced street** (parking on the near/camera side, double yellow lines on the opposite side). If your street matches this layout, you don't need to change anything.

If your street is different, update these variables in `.env`:

```bash
# Which side of the road has parking?
# "near" = camera side (same side as the house) — most UK terraced streets
# "far"  = opposite side of the road
STREET_PARKING_SIDE=near

# What restriction applies to the opposite side?
# Options: none, single_yellow, double_yellow, no_parking
OPPOSITE_SIDE_RESTRICTION=double_yellow

# Your vehicle length in metres.
# Claude uses this to decide if a gap is large enough to fit your car.
VEHICLE_LENGTH_METRES=4.5

# Minimum gap in metres to report as a free space.
# Gaps smaller than this will not be counted as available.
MIN_SPACE_METRES=5.0
```

**What the AI ignores in all cases:**
- Moving vehicles (traffic driving past — not parked)
- Vehicles on the opposite side of the road (double yellow lines)
- Foreground objects visible through the window (stone wall, wheelie bin, garden, window frame)
- Window glass reflections, glare, and condensation

---

## Step 7: Verify Camera Connectivity

Before running the full system, confirm the camera is reachable:

```bash
source venv/bin/activate

# Test pytapo connection
python -c "
from config import load_config
from camera import TapoCamera
cfg = load_config()
cam = TapoCamera(cfg)
cam.connect()
print('Camera connected and calibrated OK')
"

# Test RTSP frame grab
python -c "
from config import load_config
from camera import TapoCamera
cfg = load_config()
cam = TapoCamera(cfg)
cam.connect()
frame = cam.grab_frame()
print(f'Frame captured: {len(frame)} bytes')
"
```

If either command fails, see the **Troubleshooting** section at the bottom.

---

## Step 8: Camera Calibration (Automatic)

The system **auto-calibrates on first boot** — no manual step needed!

When you start the monitor for the first time (`python main.py`), it will:
1. Sweep the camera through 13 angles (−90° to +90°)
2. Send each frame to Claude for scoring (0–10 usefulness)
3. Select the best angles (score ≥ 6) as `SCAN_POSITIONS`
4. Pick the angle with the best view of your home spot as `HOME_POSITION`
5. Send you a Telegram summary with all scores and selected positions

> **Note**: Auto-calibration skips during night hours (quiet hours) to avoid poor-quality scores. If you reboot at 2am, it will use default positions and re-calibrate the next time it runs in daylight.

### Optional: Run calibration manually

If you want to run calibration on demand (e.g., after repositioning the camera):

```bash
# Via Telegram: send /calibrate to your bot

# Or via CLI (AI-assisted if Anthropic key configured):
source venv/bin/activate
python calibrate.py
```

The CLI tool also generates `calibration/index.html` with visual thumbnails for review:

```bash
# Copy to your Mac for review:
scp -r pi@parking-pi.local:~/smart-parking-monitor/calibration/ ~/Desktop/
```

---

## Step 9: Test Components Individually

```bash
source venv/bin/activate

# Test Claude vision API
python -c "
from config import load_config
from vision import ParkingVision
cfg = load_config()
v = ParkingVision(cfg)
print('Vision module OK')
"

# Test Telegram notification
python -c "
from config import load_config
from notifications import NotificationManager
cfg = load_config()
n = NotificationManager(cfg)
n.notify_startup()
print('Telegram notification sent — check your phone')
"

# Validate full config
python -c "
from config import load_config, validate
cfg = load_config()
ok = validate(cfg)
print('Config valid:', ok)
"

# Run one full monitoring cycle (Ctrl+C to stop after first check)
python main.py --skip-bot --skip-api
```

---

## Step 10: Pre-Flight Checklist

Before enabling the systemd service, confirm every item:

- [ ] `python -c "from config import load_config, validate; validate(load_config())"` prints `Config valid: True`
- [ ] Camera connects and grabs a frame (Step 7)
- [ ] `calibration/index.html` shows correct angles
- [ ] Telegram received a startup notification (Step 9)
- [ ] `python main.py --skip-bot --skip-api` runs without errors

---

## Step 11: Install the systemd Service

```bash
# If you used a username other than 'pi', edit the service file first:
# nano parking-monitor.service  (replace 'pi' with your username on all 4 lines)

sudo cp parking-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable parking-monitor
sudo systemctl start parking-monitor

# Verify it started successfully
sudo systemctl status parking-monitor

# Watch live logs
sudo journalctl -u parking-monitor -f
```

---

## Step 12: Set Up Tailscale

```bash
# Install Tailscale on the Pi
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Note the Pi's Tailscale IP (something like 100.x.y.z)
tailscale ip -4
```

Install **Tailscale** on your iPhone from the App Store and sign in with the same account.

Test from iPhone (turn off WiFi first, use cellular):
```
http://100.x.y.z:8080/status
```

---

## Step 13: Create the Siri Shortcut

See [docs/SIRI_SHORTCUT_GUIDE.md](docs/SIRI_SHORTCUT_GUIDE.md) for full instructions.

Quick version:
1. **Shortcuts app** → New Shortcut
2. Add Action → **"Get Contents of URL"** → `http://100.x.y.z:8080/status`
3. Add Action → **"Speak Text"** (use the result from step 2)
4. Tap shortcut name → **"Add to Siri"** → say `"Is parking free?"`

---

## Step 14: Final Verification Checklist

- [ ] `sudo systemctl status parking-monitor` shows `active (running)`
- [ ] Telegram bot responds to `/status`
- [ ] Telegram bot responds to `/scan`
- [ ] `http://100.x.y.z:8080/` returns `{"status": "ok"}`
- [ ] Siri reports parking status when asked
- [ ] Pushover notification arrives when space becomes free/occupied
- [ ] Service auto-restarts after `sudo reboot`

---

## Updating

```bash
cd ~/smart-parking-monitor
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart parking-monitor
```

---

## Useful Commands

```bash
# View live logs
sudo journalctl -u parking-monitor -f

# Restart service
sudo systemctl restart parking-monitor

# Stop service
sudo systemctl stop parking-monitor

# Check database stats
python -c "from state import ParkingState; import json; db = ParkingState('parking_history.db'); print(json.dumps(db.get_stats(), indent=2))"

# Manual status check (with venv active)
python -c "
from config import load_config
from camera import TapoCamera
from vision import ParkingVision
cfg = load_config()
cam = TapoCamera(cfg)
cam.connect()
vis = ParkingVision(cfg)
frame = cam.grab_frame()
result = vis.check_home_spot(frame)
print(result)
"
```

---

## Troubleshooting

### "VideoCapture failed to open RTSP stream"

1. Confirm RTSP is enabled in the Tapo app (⚙️ → Advanced Settings → RTSP)
2. Verify `TAPO_IP`, `TAPO_USER`, `TAPO_PASSWORD` in `.env` match your Camera Account credentials
3. Test connectivity: `ping <TAPO_IP>`
4. Check the RTSP URL manually with VLC: `rtsp://<user>:<password>@<ip>:554/stream1`

### "Cannot connect to Tapo camera" / pytapo authentication error

1. Double-check you are using **Camera Account** credentials, not your TP-Link cloud email/password
2. On some firmware versions pytapo needs the TP-Link cloud email — try that as a fallback
3. Check the camera is on the same network as the Pi: `ping <TAPO_IP>`
4. Try reinstalling pytapo: `pip install --upgrade pytapo`

### `externally-managed-environment` pip error

Raspberry Pi OS Bookworm (2023+) blocks pip from installing into the system Python.  Solution: always activate the venv first:

```bash
source ~/smart-parking-monitor/venv/bin/activate
pip install -r requirements.txt
```

If you accidentally ran pip without the venv, recreate it:

```bash
deactivate  # if in a venv
rm -rf venv
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install -r requirements.txt
```

### `ModuleNotFoundError: No module named 'cv2'`

You either:
- Created the venv **without** `--system-site-packages`, or
- Forgot to install `python3-opencv` via apt

Fix:

```bash
sudo apt install -y python3-opencv
rm -rf venv
python3 -m venv --system-site-packages venv
source venv/bin/activate
pip install -r requirements.txt
```

### Camera points in the wrong direction during scan / images look shifted

The camera uses **relative** movement internally.  `connect()` drives it to the left end-stop to establish a known position before any scan.  If positions still look wrong:

1. Run calibration again: send `/calibrate` via Telegram, or run `python calibrate.py`
2. Check the Telegram progress messages to see which angles scored well
3. If auto-calibration is selecting poor angles, adjust `CALIBRATION_MIN_USEFULNESS` in `.env`
4. Ensure nothing physically obstructs the camera at start-up (it will try to pan fully left)

### Service fails to start (`code=exited, status=200/CHDIR`)

Your username is not `pi`.  Edit the service file and replace all four occurrences of `pi` with your actual username:

```bash
# Check your username
whoami

sudo nano /etc/systemd/system/parking-monitor.service
# Replace /home/pi/ with /home/<your-username>/ and User=/Group= lines

sudo systemctl daemon-reload
sudo systemctl start parking-monitor
```

### Telegram bot not responding

1. Ensure the bot token in `.env` is correct (no leading/trailing spaces)
2. Send `/start` to the bot from the chat whose ID is in `TELEGRAM_CHAT_ID`
3. Check for errors: `sudo journalctl -u parking-monitor -n 50`

### Pushover notifications not arriving

1. Verify `PUSHOVER_USER_KEY` and `PUSHOVER_API_TOKEN` in `.env`
2. Check quiet hours: notifications are suppressed between `QUIET_HOURS_START` and `QUIET_HOURS_END`
3. Test manually:
   ```bash
   python -c "
   from config import load_config
   from notifications import NotificationManager
   cfg = load_config()
   n = NotificationManager(cfg)
   n.notify_startup()
   "
   ```
