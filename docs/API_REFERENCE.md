# API Reference

The Smart Parking Monitor HTTP API runs on port 8080 (configurable via `API_PORT`).

It is designed to be accessed securely via Tailscale VPN. There is no authentication — the network layer (Tailscale) provides security.

Base URL: `http://<tailscale-ip>:8080`

---

## Endpoints

### `GET /`

Service health ping.

**Example request:**
```
GET http://100.x.y.z:8080/
```

**Example response:**
```json
{
  "status": "ok",
  "service": "parking-monitor",
  "version": "1.0.0"
}
```

---

### `GET /status`

Check home parking spot status. Returns **plain text** suitable for Siri Shortcuts.

**Example request:**
```
GET http://100.x.y.z:8080/status
```

**Example responses:**
```
Your spot is free! No vehicles visible in the parking zone.
```
```
Your spot is occupied. A red car is parked outside the house.
```
```
Unable to determine parking status. Image quality too low.
```

---

### `GET /status/json`

Check home parking spot status. Returns full JSON.

**Example request:**
```
GET http://100.x.y.z:8080/status/json
```

**Example response:**
```json
{
  "status": "FREE",
  "confidence": "high",
  "description": "No vehicles visible in the parking zone.",
  "timestamp": "2026-03-18T08:22:07Z"
}
```

**Status values:** `FREE`, `OCCUPIED`, `UNKNOWN`
**Confidence values:** `high`, `medium`, `low`

---

### `GET /scan`

Perform a full street scan. Returns **plain text** suitable for Siri Shortcuts.

This is slow (30–60 seconds) as the camera pans through all scan positions.

**Example request:**
```
GET http://100.x.y.z:8080/scan
```

**Example responses:**
```
Your spot is taken, but there's a free space left on the street. One empty space visible between two parked cars.
```
```
No free spaces visible on the street right now.
```

---

### `GET /scan/json`

Perform a full street scan. Returns full JSON with results for each position.

**Example request:**
```
GET http://100.x.y.z:8080/scan/json
```

**Example response:**
```json
{
  "positions": [
    {
      "angle": -60,
      "position_name": "far left",
      "status": "OCCUPIED",
      "confidence": "high",
      "description": "Two cars parked bumper to bumper."
    },
    {
      "angle": -30,
      "position_name": "left",
      "status": "FREE",
      "confidence": "high",
      "description": "One empty space visible."
    },
    {
      "angle": 0,
      "position_name": "center",
      "status": "OCCUPIED",
      "confidence": "medium",
      "description": "Car partially visible."
    }
  ],
  "timestamp": "2026-03-18T08:22:07Z"
}
```

---

### `GET /snapshot`

Returns the current camera frame as a JPEG image.

**Example request:**
```
GET http://100.x.y.z:8080/snapshot
```

**Response headers:**
```
Content-Type: image/jpeg
Content-Disposition: inline; filename=snapshot.jpg
```

**Response body:** Raw JPEG bytes

---

### `GET /stats`

Returns parking statistics from the database.

**Example request:**
```
GET http://100.x.y.z:8080/stats
```

**Example response:**
```json
{
  "total_checks": 2880,
  "free_percentage": 68.5,
  "occupied_percentage": 31.5,
  "busiest_hours": [
    {"hour": 9, "count": 145},
    {"hour": 10, "count": 132}
  ],
  "freest_hours": [
    {"hour": 3, "count": 119},
    {"hour": 2, "count": 115}
  ],
  "checks_last_24h": 480,
  "state_changes_last_24h": 12,
  "last_check": {
    "timestamp": "2026-03-18 08:15:00",
    "status": "FREE"
  },
  "days_of_data": 6
}
```

---

### `GET /health`

System health check. Attempts a camera frame grab to test connectivity.

**Example request:**
```
GET http://100.x.y.z:8080/health
```

**Example response (healthy):**
```json
{
  "camera": "ok",
  "database": "ok",
  "uptime_seconds": 86400
}
```

**Example response (camera issue):**
```json
{
  "camera": "error: Failed to grab frame after 3 attempts",
  "database": "ok",
  "uptime_seconds": 3600
}
```

---

## Error Responses

All endpoints return HTTP 500 on unexpected errors, with a JSON body:

```json
{
  "error": "Description of what went wrong"
}
```

Plain-text endpoints (`/status`, `/scan`) return HTTP 500 with a plain-text error message.

---

## Notes

- **Authentication**: None. Rely on Tailscale VPN to restrict access.
- **Concurrency**: The API server runs in a separate thread from the monitoring loop. Concurrent camera access is possible but unlikely to cause issues in normal usage.
- **Timeouts**: `/scan` and `/health` may take 30–60+ seconds. Set appropriate timeouts in your Siri Shortcut (60+ seconds recommended).
- **CORS**: Not configured. Use from native apps or curl only.
