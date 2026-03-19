# Siri Shortcut Guide

Control your parking monitor with your voice: "Hey Siri, is parking free?"

---

## Prerequisites

1. **Tailscale installed** on both your iPhone and Raspberry Pi
2. Both devices connected to the same Tailscale network
3. Parking monitor service running on the Pi
4. Note your Pi's Tailscale IP address:
   ```bash
   tailscale ip -4
   # e.g. 100.101.102.103
   ```

---

## Quick Status Shortcut

This shortcut says whether your parking space is free or occupied.

### Steps

1. Open the **Shortcuts** app on your iPhone
2. Tap **+** to create a new shortcut
3. Tap **Add Action**
4. Search for **"Get Contents of URL"** and tap it
5. Tap **URL** and enter:
   ```
   http://100.101.102.103:8080/status
   ```
   (replace with your actual Tailscale IP)
6. Tap **+** (Add Action) below
7. Search for **"Speak Text"** and tap it
8. In the Speak Text action, tap the input field and select **Contents of URL** (from step 5)
9. Tap the shortcut name at the top → rename it to **"Parking"** or **"Is parking free"**
10. Tap **Add to Siri** → record your phrase: **"Is parking free"**
11. Tap **Done**

### Test it

Say: **"Hey Siri, is parking free?"**

Siri will call the API and speak the response, e.g.:
> *"Your spot is free! No vehicles visible in the parking zone."*

---

## Full Street Scan Shortcut

This shortcut scans the entire street for any free space.

> ⚠️ This takes 30–60 seconds. Set a longer timeout.

### Steps

1. Create a new Shortcut (same steps as above)
2. In the **Get Contents of URL** action:
   - URL: `http://100.101.102.103:8080/scan`
   - Tap **Show More**
   - Set **Timeout** to **90** seconds
3. Add **Speak Text** action (same as above)
4. Name it **"Scan for parking"**
5. Add to Siri: **"Find me a parking space"**

---

## Adding a Home Screen Widget
2. Tap **+** (Add Widget)
3. Find **Shortcuts**
4. Choose the **2×2** widget
5. Long-press the widget → **Edit Widget**
6. Select your parking shortcut

Tapping the widget will run the shortcut and speak the result.

---

## Apple Watch Usage

Siri Shortcuts automatically work on Apple Watch via Siri:

1. Raise your wrist
2. Say: **"Hey Siri, is parking free?"**

The spoken response will come through your watch speaker or AirPods.

You can also add a shortcut as an Apple Watch complication:
1. Watch app on iPhone → Complications
2. Add a Shortcuts complication
3. Select your parking shortcut

---

## Troubleshooting Siri Shortcuts

### "I can't reach that" or request times out

1. Is Tailscale enabled on your iPhone? Check the Tailscale app.
2. Is the Tailscale IP correct? Try opening `http://100.x.y.z:8080/` in Safari.
3. Is the parking monitor service running?
   ```bash
   sudo systemctl status parking-monitor
   ```

### Siri doesn't recognise the phrase

- Make sure "Hey Siri" is enabled: Settings → Siri & Search → Listen for "Hey Siri"
- Re-record the phrase: Shortcuts app → your shortcut → ⋯ → Add to Siri

### Siri says "Couldn't get contents" error

The shortcut action failed. Check:
1. The URL is correct (no typos in IP or port)
2. The Pi is running and on Tailscale
3. Try `http://<ip>:8080/` in Safari to confirm the API is up

### Response is too long / Siri cuts it off

The parking status API returns one sentence deliberately. If it's still being cut off, the Speak Text action can be configured to not limit length:
- Tap the Speak Text action → ensure "Wait Until Finished" is on

---

## Advanced: Automation

You can automate the parking check with Shortcuts automations:

1. Shortcuts app → **Automation** tab → **+**
2. Choose **Arrive** (when you arrive home)
3. Add your status shortcut
4. Enable "Run Immediately" (skips the confirmation)

Now whenever you arrive home, Siri automatically checks if your space is free.

---

## Using Without Tailscale VPN (Tailscale Funnel)

By default, these shortcuts only work when Tailscale VPN is active on your iPhone. With **Tailscale Funnel**, you get a public HTTPS URL that works on any network — no VPN needed.

See **[docs/TAILSCALE_FUNNEL.md](TAILSCALE_FUNNEL.md)** for full setup instructions.

Once configured, your shortcut URLs change from:
```
http://100.x.y.z:8080/status
```
to:
```
https://parking-pi.tail1234.ts.net/status?key=YOUR_API_KEY
```

The `?key=` query parameter replaces the `X-API-Key` header — Siri Shortcuts cannot set custom headers, but the query parameter works just as well over HTTPS.

---

## Conversational Voice Scan (`/scan/voice`)

For a natural, Siri-friendly street scan, use the `/scan/voice` endpoint instead of `/scan`.

This endpoint:
- First checks your home spot
- Short-circuits with "Good news — your spot is free. Head straight home." if it is free
- Otherwise performs a full street scan and returns a conversational narrative

**Example Siri Shortcut URL:**
```
http://100.x.y.z:8080/scan/voice
```
or with Funnel:
```
https://parking-pi.tail1234.ts.net/scan/voice?key=YOUR_API_KEY
```

**Example spoken responses:**

When your spot is free:
> *"Good news — your spot directly outside is free. Head straight home."*

When looking around:
> *"Checking your street now. Your spot directly outside is taken. Looking one or two cars to the left — that's taken. Looking further along on the left — there's a space there. Closest free space is further along on the left. I'd head there."*

When everything is full:
> *"Checking your street now. Your spot directly outside is taken. Looking one or two cars to the left — that's taken. Looking further along on the left — that's taken. Looking one or two cars to the right — that's taken. Looking further along on the right — that's taken. I've looked one or two cars to the left, further along on the left, one or two cars to the right, and further along on the right — the whole street looks full right now. Try again in a few minutes."*

### Creating a Voice Scan Shortcut

1. Create a new Shortcut
2. Add **Get Contents of URL**
   - URL: `http://100.x.y.z:8080/scan/voice`
   - Tap **Show More** → set **Timeout** to **90 seconds**
3. Add **Speak Text** → select **Contents of URL**
4. Name it **"Street scan"** and add to Siri: **"Find me a parking space"**
