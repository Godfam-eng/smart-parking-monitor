"""
api.py — HTTP API server for Smart Parking Monitor.

Provides Siri Shortcut-compatible endpoints and a full JSON API.
Uses aiohttp for async HTTP serving.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from aiohttp import web

from config import Config
from camera import TapoCamera
from vision import ParkingVision
from state import ParkingState

# Resolve the directory that contains this file so we can serve static assets
# regardless of the working directory the process was started from.
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

logger = logging.getLogger(__name__)

# Module-level references populated by start_api()
_config: Optional[Config] = None
_camera: Optional[TapoCamera] = None
_vision: Optional[ParkingVision] = None
_state: Optional[ParkingState] = None
_start_time: float = 0.0
_calibrator = None  # Optional[AutoCalibrator] — initialised lazily in start_api()


# ------------------------------------------------------------------
# Authentication middleware
# ------------------------------------------------------------------

@web.middleware
async def auth_middleware(request: web.Request, handler):
    """
    Optional API key authentication.

    If API_KEY is configured, every request must include either:
      - Header:      X-API-Key: <key>
      - Query param: ?key=<key>  (GET requests only — for Siri Shortcuts which can't set headers)
    Requests to / and /health are always allowed (unauthenticated health-check).
    If API_KEY is empty the middleware is a no-op (backward-compatible).
    """
    api_key = _config.API_KEY if _config else ""
    exempt = {"/", "/health", "/dashboard", "/manifest.json", "/sw.js"}
    if api_key and request.path not in exempt and not request.path.startswith("/static/"):
        provided = request.headers.get("X-API-Key", "")
        # Also accept ?key= query parameter for GET requests (Siri Shortcuts can't set headers)
        if not provided and request.method == "GET":
            provided = request.rel_url.query.get("key", "")
        if provided != api_key:
            return web.json_response({"error": "Unauthorized"}, status=401)
    return await handler(request)


# ------------------------------------------------------------------
# Route handlers
# ------------------------------------------------------------------

async def handle_root(request: web.Request) -> web.Response:
    """GET / — Service health ping."""
    return web.json_response(
        {"status": "ok", "service": "parking-monitor", "version": "1.0.0"}
    )


async def handle_status_text(request: web.Request) -> web.Response:
    """GET /status — Plain text status for Siri Shortcuts (cached, instant)."""
    current = _state.get_current_status()
    if current is None:
        return web.Response(
            text="No parking data yet. The monitor is still starting up.",
            content_type="text/plain",
        )
    status = current.get("status", "UNKNOWN")
    description = current.get("description", "")
    if status == "FREE":
        text = f"Your parking space is free. {description}"
    elif status == "OCCUPIED":
        text = f"Your parking space is occupied. {description}"
        # Append cached scan summary if available and the home spot is taken
        cached = _state.get_scan_cache(_config.SCAN_CACHE_MAX_AGE) if _state and _config else None
        if cached:
            scan_summary = cached.get("summary", "")
            age = cached.get("age_seconds", 0)
            if scan_summary:
                text += f" Based on my scan {_format_minutes_ago(age)}: {scan_summary}."
    else:
        text = f"Parking status is unclear. {description}"
    return web.Response(text=text, content_type="text/plain")


async def handle_status_json(request: web.Request) -> web.Response:
    """GET /status/json — Cached JSON status (instant, no Claude call)."""
    current = _state.get_current_status()
    if current is None:
        return web.json_response(
            {"status": "UNKNOWN", "description": "No parking data yet. The monitor is still starting up."},
            status=503,
        )
    cached = _state.get_scan_cache(_config.SCAN_CACHE_MAX_AGE) if _state and _config else None
    return web.json_response(
        {
            "status": current.get("status", "UNKNOWN"),
            "confidence": current.get("confidence", "low"),
            "description": current.get("description", ""),
            "timestamp": current.get("timestamp", ""),
            "scan_cache": {
                "available": cached is not None,
                "age_seconds": cached["age_seconds"] if cached else None,
                "summary": cached["summary"] if cached else None,
                "positions": cached["positions"] if cached else [],
            },
        }
    )


async def handle_status_live_text(request: web.Request) -> web.Response:
    """GET /status/live — Fresh Claude call (slow, costs money; use sparingly)."""
    try:
        loop = asyncio.get_running_loop()
        image_bytes = await loop.run_in_executor(None, _camera.grab_frame)
        result = await loop.run_in_executor(None, _vision.check_home_spot, image_bytes)
        status = result.get("status", "UNKNOWN")
        description = result.get("description", "")

        if status == "FREE":
            text = f"Your spot is free! {description}"
        elif status == "OCCUPIED":
            text = f"Your spot is occupied. {description}"
        else:
            text = f"Unable to determine parking status. {description}"

        return web.Response(text=text, content_type="text/plain")

    except Exception as exc:
        logger.error("Error in /status/live handler: %s", exc)
        return web.Response(
            text=f"Error checking parking status: {exc}",
            status=500,
            content_type="text/plain",
        )


async def handle_status_live_json(request: web.Request) -> web.Response:
    """GET /status/live/json — Fresh Claude call, full JSON response."""
    try:
        loop = asyncio.get_running_loop()
        image_bytes = await loop.run_in_executor(None, _camera.grab_frame)
        result = await loop.run_in_executor(None, _vision.check_home_spot, image_bytes)
        return web.json_response(
            {
                "status": result.get("status", "UNKNOWN"),
                "confidence": result.get("confidence", "low"),
                "description": result.get("description", ""),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
    except Exception as exc:
        logger.error("Error in /status/live/json handler: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


async def handle_scan_text(request: web.Request) -> web.Response:
    """GET /scan — Plain text scan result for Siri Shortcuts."""
    try:
        loop = asyncio.get_running_loop()
        positions = await loop.run_in_executor(None, _camera.scan_street)
        if not positions:
            return web.Response(
                text="Could not capture scan images.",
                content_type="text/plain",
                status=500,
            )

        async def _analyse_position(pos):
            result = await loop.run_in_executor(
                None, _vision.check_scan_position, pos["image"], pos["position_name"]
            )
            return {**pos, **result}

        analysed = await asyncio.gather(*[_analyse_position(p) for p in positions])

        free_positions = [a for a in analysed if a.get("status") == "FREE"]

        if free_positions:
            first = free_positions[0]
            pos_name = first["position_name"]
            description = first.get("description", "")
            text = (
                f"Your spot is taken, but there's a free space {pos_name} on the street. "
                f"{description}"
            )
        else:
            text = "No free spaces visible on the street right now."

        return web.Response(text=text, content_type="text/plain")

    except Exception as exc:
        logger.error("Error in /scan handler: %s", exc)
        return web.Response(
            text=f"Error scanning street: {exc}",
            status=500,
            content_type="text/plain",
        )


async def handle_scan_json(request: web.Request) -> web.Response:
    """GET /scan/json — Full JSON scan results."""
    try:
        loop = asyncio.get_running_loop()
        positions = await loop.run_in_executor(None, _camera.scan_street)

        async def _analyse_position(pos):
            result = await loop.run_in_executor(
                None, _vision.check_scan_position, pos["image"], pos["position_name"]
            )
            return {
                "angle": pos["angle"],
                "position_name": pos["position_name"],
                "status": result.get("status", "UNKNOWN"),
                "confidence": result.get("confidence", "low"),
                "description": result.get("description", ""),
            }

        results = await asyncio.gather(*[_analyse_position(p) for p in positions])

        return web.json_response(
            {"positions": list(results), "timestamp": datetime.now(timezone.utc).isoformat()}
        )

    except Exception as exc:
        logger.error("Error in /scan/json handler: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


# ------------------------------------------------------------------
# Voice narrative helpers
# ------------------------------------------------------------------

def _format_minutes_ago(age_seconds: int) -> str:
    """Return a human-readable string like '4 minutes ago' or '1 minute ago'."""
    minutes = age_seconds // 60
    return f"{minutes} minute{'s' if minutes != 1 else ''} ago"


def _build_home_result_from_cache(cached_positions: list) -> dict:
    """Extract a home-position result dict from a cached scan's position list.

    Looks for the 'center' position entry and returns it in the same format as
    ``vision.check_home_spot()``.  Falls back to an UNKNOWN result if no center
    position is present in the cache.
    """
    center = next(
        (p for p in cached_positions if p.get("position_name") == "center"),
        None,
    )
    if center:
        return {
            "status": center.get("status", "UNKNOWN"),
            "confidence": center.get("confidence", "low"),
            "description": center.get("description", ""),
        }
    return {"status": "UNKNOWN", "confidence": "low", "description": ""}

_POSITION_PHRASES = {
    "center":    "your spot directly outside",
    "left":      "one or two cars to the left",
    "far left":  "further along on the left",
    "right":     "one or two cars to the right",
    "far right": "further along on the right",
}

_STATUS_PHRASES = {
    "FREE":     "there's a space there",
    "OCCUPIED": "that's taken",
    "UNKNOWN":  "I can't quite tell",
}

# Narrative order: home first, then nearest-to-furthest on each side
_POSITION_ORDER = ["center", "left", "far left", "right", "far right"]


def _build_voice_narrative(home_result: dict, scan_results: list) -> str:
    """Build a conversational spoken narrative from scan results.

    Args:
        home_result: Vision result dict for the home/center position.
        scan_results: List of dicts with keys 'position_name', 'status', 'description'.

    Returns:
        A plain-text narrative string suitable for Siri to read aloud.
    """
    lines = ["Checking your street now."]

    home_status = home_result.get("status", "UNKNOWN")
    home_phrase = _POSITION_PHRASES.get("center", "your spot directly outside")

    if home_status == "FREE":
        lines.append(f"Your spot directly outside is free.")
    elif home_status == "OCCUPIED":
        lines.append(f"Your spot directly outside is taken.")
    else:
        lines.append(f"I can't quite tell about your spot directly outside.")

    # Build a map of position_name → result for easy lookup
    scan_map = {r["position_name"].lower(): r for r in scan_results}

    # Walk positions in narrative order (skip 'center' — already handled above)
    free_spaces = []
    for pos_key in _POSITION_ORDER[1:]:
        result = scan_map.get(pos_key)
        if result is None:
            continue
        status = result.get("status", "UNKNOWN")
        description = result.get("description", "")
        status_phrase = _STATUS_PHRASES.get(status, "I can't quite tell")
        phrase = _POSITION_PHRASES.get(pos_key, pos_key)
        line = f"Looking {phrase} — {status_phrase}."

        if status == "FREE":
            if description:
                line += f" {description}"
            free_spaces.append((pos_key, phrase))
        lines.append(line)

    # Summary
    if home_status == "FREE":
        lines.append("Your spot is right there. Head straight home.")
    elif free_spaces:
        nearest_phrase = free_spaces[0][1]
        lines.append(f"Closest free space is {nearest_phrase}. I'd head there.")
    else:
        all_positions = [_POSITION_PHRASES.get(p, p) for p in _POSITION_ORDER[1:] if p in scan_map]
        if all_positions:
            pos_list = ", ".join(all_positions[:-1])
            if len(all_positions) > 1:
                pos_list += f", and {all_positions[-1]}"
            else:
                pos_list = all_positions[0]
            lines.append(
                f"I've looked {pos_list} — the whole street looks full right now. "
                "Try again in a few minutes."
            )
        else:
            lines.append("The whole street looks full right now. Try again in a few minutes.")

    return " ".join(lines)


async def handle_scan_voice(request: web.Request) -> web.Response:
    """GET /scan/voice — Conversational plain-text scan result for Siri."""
    try:
        loop = asyncio.get_running_loop()

        # Step 1: Check if a fresh scan cache is available.
        cached = _state.get_scan_cache(_config.SCAN_CACHE_MAX_AGE) if _state and _config else None
        if cached:
            cached_positions = cached.get("positions", [])
            cached_summary = cached.get("summary", "")
            age = cached.get("age_seconds", 0)
            logger.info("Serving /scan/voice from cache (age=%ds)", age)
            home_result = _build_home_result_from_cache(cached_positions)
            narrative = _build_voice_narrative(home_result, cached_positions)
            suffix = f" (Street data from {_format_minutes_ago(age)}.)"
            return web.Response(text=narrative + suffix, content_type="text/plain")

        # Step 2: Check home spot first (fast path — skip full scan if home is free)
        image_bytes = await loop.run_in_executor(None, _camera.grab_frame)
        home_result = await loop.run_in_executor(None, _vision.check_home_spot, image_bytes)

        home_status = home_result.get("status", "UNKNOWN")
        home_confidence = home_result.get("confidence", "low")

        if home_status == "FREE" and home_confidence in ("high", "medium"):
            text = "Good news — your spot directly outside is free. Head straight home."
            return web.Response(text=text, content_type="text/plain")

        # Step 3: Full street scan with early exit on first confident free space.
        # Capture all frames first using the iterator, then analyse in parallel.
        positions = []
        for pos_data in _camera.scan_street_iter():
            positions.append(pos_data)

        if not positions:
            return web.Response(
                text="Sorry, I couldn't scan the street right now.",
                status=500,
                content_type="text/plain",
            )

        async def _analyse_position(pos):
            result = await loop.run_in_executor(
                None, _vision.check_scan_position, pos["image"], pos["position_name"]
            )
            return {
                "position_name": pos["position_name"],
                "status": result.get("status", "UNKNOWN"),
                "confidence": result.get("confidence", "low"),
                "description": result.get("description", ""),
            }

        scan_results = list(await asyncio.gather(*[_analyse_position(p) for p in positions]))

        # Step 4: Build narrative
        narrative = _build_voice_narrative(home_result, scan_results)
        return web.Response(text=narrative, content_type="text/plain")

    except Exception as exc:
        logger.error("Error in /scan/voice handler: %s", exc)
        return web.Response(
            text=f"Sorry, I couldn't check the street right now: {exc}",
            status=500,
            content_type="text/plain",
        )


async def handle_snapshot(request: web.Request) -> web.Response:
    """GET /snapshot — Return current JPEG frame."""
    try:
        loop = asyncio.get_running_loop()
        image_bytes = await loop.run_in_executor(None, _camera.get_snapshot)
        return web.Response(
            body=image_bytes,
            content_type="image/jpeg",
            headers={"Content-Disposition": "inline; filename=snapshot.jpg"},
        )
    except Exception as exc:
        logger.error("Error in /snapshot handler: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


async def handle_stats(request: web.Request) -> web.Response:
    """GET /stats — JSON statistics from database."""
    try:
        stats = _state.get_stats()
        return web.json_response(stats)
    except Exception as exc:
        logger.error("Error in /stats handler: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


async def handle_health(request: web.Request) -> web.Response:
    """GET /health — Liveness / readiness check."""
    loop = asyncio.get_running_loop()

    # Check camera
    camera_ok = "ok"
    try:
        await loop.run_in_executor(None, _camera.grab_frame)
    except Exception as exc:
        camera_ok = f"error: {exc}"

    # Check database
    db_ok = "ok"
    try:
        _state.get_current_status()
    except Exception as exc:
        db_ok = f"error: {exc}"

    uptime = int(time.time() - _start_time) if _start_time else 0

    watch = _state.get_watch_mode() if _state else None

    return web.json_response(
        {
            "camera": camera_ok,
            "database": db_ok,
            "uptime_seconds": uptime,
            "watch_mode": {
                "active": watch is not None,
                "mode": watch["mode"] if watch else None,
                "expires_at": watch["expires_at"] if watch else None,
            },
        }
    )


async def handle_calibrate(request: web.Request) -> web.Response:
    """POST /calibrate — Trigger auto-calibration sweep (blocking, may take minutes)."""
    if _calibrator is None:
        return web.json_response(
            {"error": "Calibration not available — camera or AI not initialised"},
            status=503,
        )
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, _calibrator.run_calibration)
        return web.json_response(
            {
                "home_position": result.home_position,
                "scan_positions": result.scan_positions,
                "parking_side": result.parking_side,
                "opposite_restriction": result.opposite_restriction,
                "timestamp": result.timestamp,
                "angle_count": len(result.angle_scores),
            }
        )
    except Exception as exc:
        logger.error("Error in POST /calibrate handler: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


async def handle_calibration_status(request: web.Request) -> web.Response:
    """GET /calibration — Return the most recent calibration data."""
    try:
        cal = _state.get_latest_calibration()
        if cal is None:
            return web.json_response(
                {"status": "uncalibrated", "message": "No calibration found. POST /calibrate to run."},
                status=404,
            )
        return web.json_response(cal)
    except Exception as exc:
        logger.error("Error in GET /calibration handler: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


# ------------------------------------------------------------------
# PWA Dashboard handlers
# ------------------------------------------------------------------

def _read_static(filename: str) -> bytes:
    """Read a file from the static/ directory and return its contents."""
    path = os.path.join(_STATIC_DIR, filename)
    with open(path, "rb") as fh:
        return fh.read()


async def handle_dashboard(request: web.Request) -> web.Response:
    """GET /dashboard — Serve the PWA dashboard HTML (no auth required)."""
    try:
        body = _read_static("dashboard.html")
        return web.Response(body=body, content_type="text/html")
    except FileNotFoundError:
        return web.Response(text="Dashboard not found.", status=404, content_type="text/plain")


async def handle_manifest(request: web.Request) -> web.Response:
    """GET /manifest.json — Serve the PWA web-app manifest (no auth required)."""
    try:
        body = _read_static("manifest.json")
        return web.Response(body=body, content_type="application/manifest+json")
    except FileNotFoundError:
        return web.json_response({"error": "manifest.json not found"}, status=404)


async def handle_sw(request: web.Request) -> web.Response:
    """GET /sw.js — Serve the service worker (no auth required)."""
    try:
        body = _read_static("sw.js")
        return web.Response(
            body=body,
            content_type="application/javascript",
            headers={"Service-Worker-Allowed": "/"},
        )
    except FileNotFoundError:
        return web.Response(text="// sw.js not found", status=404, content_type="application/javascript")


async def handle_config(request: web.Request) -> web.Response:
    """GET /config — Return non-sensitive configuration as JSON."""
    cfg = _config
    if cfg is None:
        return web.json_response({"error": "Config not initialised"}, status=503)
    return web.json_response(
        {
            "check_interval": cfg.CHECK_INTERVAL,
            "quiet_hours_start": cfg.QUIET_HOURS_START,
            "quiet_hours_end": cfg.QUIET_HOURS_END,
            "parking_zone_top": cfg.PARKING_ZONE_TOP,
            "parking_zone_bottom": cfg.PARKING_ZONE_BOTTOM,
            "parking_zone_left": cfg.PARKING_ZONE_LEFT,
            "parking_zone_right": cfg.PARKING_ZONE_RIGHT,
            "scan_positions": cfg.SCAN_POSITIONS,
            "confidence_threshold": cfg.CONFIDENCE_THRESHOLD,
            "home_position": cfg.HOME_POSITION,
            "api_port": cfg.API_PORT,
            "street_parking_side": cfg.STREET_PARKING_SIDE,
            "opposite_side_restriction": cfg.OPPOSITE_SIDE_RESTRICTION,
        }
    )


async def handle_history(request: web.Request) -> web.Response:
    """GET /history — Return hourly breakdown data combined with overall stats."""
    try:
        hours = _state.get_hourly_breakdown()
        stats = _state.get_stats()
        return web.json_response({"hours": hours, "stats": stats})
    except Exception as exc:
        logger.error("Error in /history handler: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


# ------------------------------------------------------------------
# App factory
# ------------------------------------------------------------------

def _build_app() -> web.Application:
    """Create and configure the aiohttp Application."""
    app = web.Application(middlewares=[auth_middleware])
    app.router.add_get("/", handle_root)
    app.router.add_get("/status", handle_status_text)
    app.router.add_get("/status/json", handle_status_json)
    app.router.add_get("/status/live", handle_status_live_text)
    app.router.add_get("/status/live/json", handle_status_live_json)
    app.router.add_get("/scan", handle_scan_text)
    app.router.add_get("/scan/json", handle_scan_json)
    app.router.add_get("/scan/voice", handle_scan_voice)
    app.router.add_get("/snapshot", handle_snapshot)
    app.router.add_get("/stats", handle_stats)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/calibrate", handle_calibrate)
    app.router.add_get("/calibration", handle_calibration_status)
    # PWA dashboard routes
    app.router.add_get("/dashboard", handle_dashboard)
    app.router.add_get("/manifest.json", handle_manifest)
    app.router.add_get("/sw.js", handle_sw)
    app.router.add_get("/config", handle_config)
    app.router.add_get("/history", handle_history)
    return app


# ------------------------------------------------------------------
# Public entry point (called from main.py)
# ------------------------------------------------------------------

def start_api(
    cfg: Config,
    camera: TapoCamera,
    vision: ParkingVision,
    state: ParkingState,
) -> None:
    """
    Initialise module globals and start the aiohttp server.

    Intended to be called in a daemon thread from main.py.
    Also works standalone: ``python api.py``
    """
    global _config, _camera, _vision, _state, _start_time, _calibrator
    _config = cfg
    _camera = camera
    _vision = vision
    _state = state
    _start_time = time.time()

    # Initialise the calibrator lazily to avoid circular imports at module level
    try:
        from auto_calibrate import AutoCalibrator
        _calibrator = AutoCalibrator(camera, vision, state)
    except Exception as exc:
        logger.warning("Could not initialise AutoCalibrator in API: %s", exc)
        _calibrator = None

    logger.info("Starting HTTP API on %s:%d", cfg.API_HOST, cfg.API_PORT)
    app = _build_app()

    # Run inside a new event loop (this function runs in a thread).
    # handle_signals=False prevents aiohttp from trying to install signal
    # handlers, which only work in the main thread and would raise
    # ValueError: set_wakeup_fd only works in main thread of the main interpreter.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    web.run_app(app, host=cfg.API_HOST, port=cfg.API_PORT, access_log=None, handle_signals=False)


# ------------------------------------------------------------------
# Standalone entry point
# ------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from config import load_config, validate
    from camera import TapoCamera
    from vision import ParkingVision
    from state import ParkingState

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cfg = load_config()
    if not validate(cfg):
        sys.exit(1)

    cam = TapoCamera(cfg)
    vis = ParkingVision(cfg)
    db = ParkingState(cfg.DB_PATH)

    start_api(cfg, cam, vis, db)
