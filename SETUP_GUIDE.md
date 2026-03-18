# Smart Parking Monitor — Complete Setup Guide

This guide takes you from zero to a fully operational parking monitor running 24/7 on a Raspberry Pi 5.

---

## Pre-Arrival Checklist

Before you start, create accounts and gather credentials for these services:

- [ ] **Anthropic** — https://console.anthropic.com (Claude API key)
- [ ] **Telegram** — Create a bot via [@BotFather](https://t.me/botfather), get token + chat ID
- [ ] **Pushover** — https://pushover.net (user key + create an app for API token)
- [ ] **Tailscale** — https://tailscale.com (free account, install on Pi and iPhone)
- [ ] **Tapo C225** — Enable RTSP in the Tapo app: Camera Settings → Advanced → RTSP

---

## Step 1: Flash the SD Card

1. Download [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Choose **Raspberry Pi OS Lite (64-bit)** — no desktop needed
3. Click the ⚙️ gear icon to configure:
   - Enable SSH (use password authentication)
   - Set username: `pi` and a strong password
   - Set hostname: `parking-pi`
   - Configure WiFi (or skip if using Ethernet)
4. Flash the card and insert into Pi

---

## Step 2: First Boot and SSH

```bash
# From your Mac/PC on the same network:
ssh pi@parking-pi.local

# If hostname doesn't resolve, find the IP from your router and use:
ssh pi@192.168.1.xxx
```

---

## Step 3: System Updates

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-pip python3-venv python3-opencv git libopencv-dev
```

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
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Step 6: Configure

```bash
cp .env.example .env
nano .env
```

Fill in every value. At minimum:
- `TAPO_IP` — Find this in your router's DHCP table or the Tapo app
- `TAPO_USER` / `TAPO_PASSWORD` — Tapo account credentials
- `ANTHROPIC_API_KEY` — From https://console.anthropic.com
- `TELEGRAM_BOT_TOKEN` — From BotFather
- `TELEGRAM_CHAT_ID` — Send a message to your bot, then visit:
  `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your chat ID

---

## Step 7: Calibrate the Camera

With the camera positioned at your window:

```bash
source venv/bin/activate
python calibrate.py
```

This sweeps from -90° to +90° and saves images to `calibration/`.

Open `calibration/index.html` in a browser (copy the directory to your Mac with `scp`):

```bash
# On your Mac:
scp -r pi@parking-pi.local:~/smart-parking-monitor/calibration/ ~/Desktop/
```

Open `~/Desktop/calibration/index.html` and note which angles show useful street coverage.
Update `SCAN_POSITIONS` in `.env` accordingly.

---

## Step 8: Test Components Individually

```bash
# Test camera connection
python -c "from config import config; from camera import TapoCamera; c = TapoCamera(config); c.connect(); print('Camera OK:', len(c.grab_frame()), 'bytes')"

# Test Claude API
python -c "from config import config; from vision import ParkingVision; v = ParkingVision(config); print('Vision OK')"

# Test notifications
python -c "from config import config; from notifications import NotificationManager; n = NotificationManager(config); n.notify_startup()"
# Check Telegram for startup message

# Run main loop (no bot/API, 1 check then Ctrl+C)
python main.py --skip-bot --skip-api
```

---

## Step 9: Install the systemd Service

```bash
# Install the service file
sudo cp parking-monitor.service /etc/systemd/system/

# Reload systemd and enable on boot
sudo systemctl daemon-reload
sudo systemctl enable parking-monitor

# Start it now
sudo systemctl start parking-monitor

# Check it's running
sudo systemctl status parking-monitor

# Watch live logs
sudo journalctl -u parking-monitor -f
```

---

## Step 10: Set Up Tailscale

```bash
# Install Tailscale on Pi
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Note the Pi's Tailscale IP (something like 100.x.y.z)
tailscale ip -4
```

Install Tailscale on your iPhone from the App Store and sign in with the same account.

Test from iPhone (over 4G, not WiFi):
```
http://100.x.y.z:8080/status
```

---

## Step 11: Create the Siri Shortcut

See [docs/SIRI_SHORTCUT_GUIDE.md](docs/SIRI_SHORTCUT_GUIDE.md) for full instructions.

Quick version:
1. Shortcuts app → New Shortcut
2. Add Action → "Get Contents of URL" → `http://100.x.y.z:8080/status`
3. Add Action → "Speak Text" (use the result from step 2)
4. Tap shortcut name → "Add to Siri" → say "Is parking free?"

---

## Step 12: Verification Checklist

- [ ] `sudo systemctl status parking-monitor` shows `active (running)`
- [ ] Telegram bot responds to `/status`
- [ ] Telegram bot responds to `/scan`
- [ ] `http://100.x.y.z:8080/` returns `{"status": "ok"}`
- [ ] Siri says parking status when asked
- [ ] Pushover notification appears when space becomes free
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
# View logs
sudo journalctl -u parking-monitor -f

# Restart service
sudo systemctl restart parking-monitor

# Stop service
sudo systemctl stop parking-monitor

# Check database stats
python -c "from state import ParkingState; db = ParkingState('parking_history.db'); import json; print(json.dumps(db.get_stats(), indent=2))"
```
