# Troubleshooting Guide

---

## Camera Won't Connect

**Symptom:** `ConnectionError: Cannot connect to Tapo camera`

**Checks:**
1. Is the camera powered on and showing a solid LED?
2. Is the Pi on the same network as the camera?
   ```bash
   ping 192.168.1.100  # use your camera's IP
   ```
3. Is the RTSP stream enabled in the Tapo app?
   - Open Tapo app → Camera → Settings → Advanced Settings → RTSP
   - Enable it and note the credentials
4. Test RTSP manually:
   ```bash
   ffplay rtsp://admin:password@192.168.1.100:554/stream1
   ```
5. Check firewall: the Pi needs to reach port 554 on the camera's IP.
6. Verify `TAPO_IP`, `TAPO_USER`, `TAPO_PASSWORD` in `.env` match exactly.

---

## Camera Connects But grab_frame() Fails

**Symptom:** `RuntimeError: Failed to grab frame after 3 attempts`

**Checks:**
1. Increase `SCAN_SETTLE_TIME` in `.env` (try 5.0)
2. Try the RTSP URL manually with ffplay
3. Check if OpenCV was compiled with RTSP support:
   ```bash
   python -c "import cv2; print(cv2.getBuildInformation())" | grep -i rtsp
   ```
4. Ensure `opencv-python-headless` is installed (not `opencv-python`)
5. Reduce camera resolution in Tapo app if bandwidth is an issue

---

## Claude API Errors

**Symptom:** `status: UNKNOWN` with description `Authentication error`

**Check:** Is `ANTHROPIC_API_KEY` correct and does your account have API credits?
```bash
python -c "
import anthropic, os
from dotenv import load_dotenv
load_dotenv()
client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
print('API key OK' if client else 'Failed')
"
```

**Symptom:** `Rate limit — try again shortly`

The free tier has low rate limits. Either:
- Increase `CHECK_INTERVAL` (e.g. 300 seconds)
- Upgrade to a paid Anthropic plan

**Symptom:** `API timeout`

Claude occasionally times out under heavy load. The system will retry on the next cycle automatically.

---

## No Telegram Messages

**Symptom:** Bot started but no messages received

1. Did you message the bot first? Bots can't initiate conversations unless you've sent them at least one message.
2. Is `TELEGRAM_BOT_TOKEN` correct? Test:
   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/getMe"
   ```
3. Is `TELEGRAM_CHAT_ID` correct? After messaging the bot, check:
   ```bash
   curl "https://api.telegram.org/bot<TOKEN>/getUpdates"
   ```
   Look for `"id"` inside `"chat"`.
4. Check logs:
   ```bash
   sudo journalctl -u parking-monitor -f
   ```

---

## No Pushover Notifications

**Symptom:** Telegram works but Pushover doesn't

1. Is it quiet hours? Check `QUIET_HOURS_START` and `QUIET_HOURS_END`
2. Are `PUSHOVER_USER_KEY` and `PUSHOVER_API_TOKEN` correct?
3. Test manually:
   ```bash
   python -c "
   from config import config
   from notifications import NotificationManager
   n = NotificationManager(config)
   n.send_pushover('Test', 'Test from parking monitor')
   "
   ```
4. Check Pushover app settings: are notifications allowed for the app?

---

## Service Won't Start

**Symptom:** `sudo systemctl start parking-monitor` fails immediately

1. Check the service file is correct:
   ```bash
   sudo systemctl status parking-monitor
   ```
2. View detailed logs:
   ```bash
   sudo journalctl -u parking-monitor -n 50
   ```
3. Common causes:
   - Wrong `WorkingDirectory` path
   - venv not at `/home/pi/smart-parking-monitor/venv/`
   - `.env` file not present
   - Python syntax error in a source file
4. Test manually first:
   ```bash
   cd ~/smart-parking-monitor
   source venv/bin/activate
   python main.py --skip-bot --skip-api
   ```

---

## High Claude API Costs

With a 180-second interval and no cost controls, the API can cost £5–£10/day. The system now includes several cost-reduction features that are enabled by default.

**Quick checks:**
1. Is `CHECK_INTERVAL` set to a sensible value? 600 seconds (10 min) is the new default — 180 s was the old default and caused high spend.
2. Is `MOTION_GATE_ENABLED=true`? This skips Claude when the camera view hasn't changed (most cycles when a car is parked or the street is consistently occupied).
3. Is `CLAUDE_MODEL_FAST` set? Background checks now use `claude-haiku-3-5-20241022` (~15× cheaper than Sonnet).
4. Is `VISION_RESIZE_WIDTH=640` and `VISION_RESIZE_HEIGHT=480`? Full 2K images cost ~4,900 image tokens; 640×480 costs ~180 tokens.

**Cost estimate with default settings:**
- `CHECK_INTERVAL=600`: ~144 checks/day
- Motion gate skips ~80–90% when parked: ~15–30 Claude calls/day
- Haiku + small images: ~$0.002/day

See the `# --- Cost Reduction (Claude API) ---` section in `.env.example` for all options.

---

## Siri Shortcut Works on WiFi But Fails on 4G

iOS App Transport Security (ATS) blocks `http://` requests to non-local addresses on cellular data. The fix is to use HTTPS via Tailscale Funnel.

**Symptoms:**
- "The request was not allowed by ATS"
- "Could not connect to the server"
- Works perfectly on home WiFi, fails as soon as you leave

**Fix:** Switch your Siri Shortcut URL from `http://` to `https://` using Tailscale Funnel:

```
https://YOUR-PI-HOSTNAME.ts.net/status?key=YOUR_API_KEY
```

See **[docs/TAILSCALE_FUNNEL.md](TAILSCALE_FUNNEL.md)** for full setup instructions, including the "WiFi works but 4G fails" section.

---

## Poor Detection Accuracy

**Symptom:** Claude frequently returns wrong status

1. **Window reflections**: Ensure the camera prompt is working. Check the parking zone configuration covers only the road area, not your interior.
2. **Adjust parking zone**: Use `calibrate.py` to see exactly what the camera sees, then adjust `PARKING_ZONE_TOP/BOTTOM/LEFT/RIGHT` percentages to frame just the parking space.
3. **Night-time accuracy**: The C225 has IR night vision but image quality drops. Consider increasing `CONFIDENCE_THRESHOLD` to `high` to avoid false alerts at night.
4. **Parked cars just outside zone**: Adjust the zone percentages.

---

## Camera Image Blurry After Move

**Symptom:** Scan images are motion-blurred

Increase `SCAN_SETTLE_TIME` in `.env`:
```
SCAN_SETTLE_TIME=4.0
```

The camera motor takes 1–3 seconds to reach position and stabilise. 2.5 seconds is usually enough but older cameras or longer pan angles may need more.

---

## Tailscale Not Working

**Symptom:** Can't reach Pi from iPhone over 4G

1. Is Tailscale running on the Pi?
   ```bash
   tailscale status
   ```
2. Is Tailscale running on your iPhone? (Check the app — it should show "Connected")
3. Are both devices in the same Tailscale account?
4. Use the Tailscale IP (100.x.y.z), not the local IP:
   ```bash
   tailscale ip -4
   ```
5. Check firewall: Pi's firewall should allow port 8080 from Tailscale:
   ```bash
   sudo ufw allow from 100.64.0.0/10 to any port 8080
   ```

---

## Database Gets Too Large

The database auto-cleans records older than 90 days (configurable). If it grows large:

```bash
# Check size
ls -lh parking_history.db

# Manually vacuum
python -c "
import sqlite3
conn = sqlite3.connect('parking_history.db')
conn.execute('VACUUM')
conn.close()
print('Done')
"
```

---

## Getting Help

1. Check logs first: `sudo journalctl -u parking-monitor -f`
2. Check this guide
3. Open an issue on GitHub with your log output (redact any API keys)
