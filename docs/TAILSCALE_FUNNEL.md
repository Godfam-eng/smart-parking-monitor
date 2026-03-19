# Tailscale Funnel — Public HTTPS Access

Tailscale Funnel lets you expose your parking monitor to the public internet over HTTPS — no port forwarding, no firewall changes, no VPN required on your iPhone.

This means your Siri Shortcut works even when Tailscale is not active on your phone.

---

## How It Works

Tailscale Funnel routes HTTPS traffic from a public URL (e.g. `https://parking-pi.tail1234.ts.net`) through Tailscale's infrastructure to your Raspberry Pi. The Pi never needs to open a port to the internet directly.

**Security note:** Because the URL is public, always set `API_KEY` in your `.env` file before enabling Funnel. Unauthenticated access means anyone who guesses your URL can check your parking status.

---

## Prerequisites

1. Tailscale installed and running on your Raspberry Pi
2. Tailscale account with Funnel enabled (free for personal use)
3. Smart Parking Monitor running (via `python main.py` or systemd)

---

## Step-by-Step Setup

### 1. Enable Tailscale Funnel

On your Raspberry Pi, run:

```bash
tailscale funnel 8080
```

This exposes port 8080 on your Pi as a public HTTPS endpoint.

To make it persistent across reboots:

```bash
tailscale funnel --bg 8080
```

### 2. Get Your Public URL

```bash
tailscale funnel status
```

You will see output like:

```
https://parking-pi.tail1234.ts.net/
└── proxy http://localhost:8080
```

Your public URL is `https://parking-pi.tail1234.ts.net` — note it down.

You can also find this in the [Tailscale admin console](https://login.tailscale.com/admin/machines) under your Pi's machine details.

### 3. Set Your API Key

Edit your `.env` file on the Pi:

```bash
nano .env
```

Add or update:

```bash
API_KEY=your-strong-secret-key-here
PUBLIC_URL=https://parking-pi.tail1234.ts.net
```

Restart the service:

```bash
sudo systemctl restart parking-monitor
```

### 4. Update Your Siri Shortcut

In the Shortcuts app on your iPhone:

1. Open your existing parking shortcut (or create a new one)
2. Change the URL from `http://100.x.y.z:8080/status` to:
   ```
   https://parking-pi.tail1234.ts.net/status?key=your-strong-secret-key-here
   ```
3. Tap **Done**

The `?key=` query parameter is how Siri Shortcuts authenticate — they cannot set HTTP headers.

### 5. Test Without VPN

Turn off Tailscale on your iPhone, then say: **"Hey Siri, is parking free?"**

It should work over your normal cellular or Wi-Fi connection.

---

## Updating Your PWA Dashboard

If you have the dashboard installed on your home screen, reinstall it from the new public URL:

1. Open Safari on your iPhone
2. Navigate to: `https://parking-pi.tail1234.ts.net/dashboard`
3. Tap **Share** → **Add to Home Screen**
4. Replace the old shortcut

The dashboard will now load without requiring Tailscale VPN.

---

## Siri Shortcut Examples

| Use case | URL |
|----------|-----|
| Quick status (cached) | `https://your-pi.ts.net/status?key=YOUR_KEY` |
| Full street scan | `https://your-pi.ts.net/scan?key=YOUR_KEY` |
| Conversational voice scan | `https://your-pi.ts.net/scan/voice?key=YOUR_KEY` |
| Live status (fresh Claude call) | `https://your-pi.ts.net/status/live?key=YOUR_KEY` |

---

## Security Considerations

- **Always set `API_KEY`** when using Funnel. Without it, anyone can access your camera feed and parking status.
- **Never commit your API key** to source control — keep it in `.env` only.
- The `?key=` query parameter is transmitted over HTTPS (encrypted), so it is safe for use in Siri Shortcuts.
- Funnel URLs are guessable if someone knows your Tailnet name. Use a strong, random API key (16+ characters).
- You can revoke access at any time by changing `API_KEY` in `.env` and restarting the service.

---

## Disabling Funnel

To turn off public access:

```bash
tailscale funnel off
```

Your Pi reverts to Tailscale VPN-only access.

---

## Troubleshooting

### "Can't connect" after setting up Funnel

1. Check Funnel is active: `tailscale funnel status`
2. Confirm the parking monitor is running: `sudo systemctl status parking-monitor`
3. Test locally first: `curl http://localhost:8080/`
4. Check your API key is set and matches in both `.env` and your Siri Shortcut URL

### "Unauthorized" error

Your `?key=` value doesn't match `API_KEY` in `.env`. Check for typos.

### Funnel URL not appearing

Funnel requires Tailscale to be authenticated. Run `tailscale status` to confirm the device is connected.
