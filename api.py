"""
api.py — HTTP API server for Smart Parking Monitor.

Provides Siri Shortcut-compatible endpoints and a full JSON API.
Uses aiohttp for async HTTP serving.
"""

import asyncio
import logging
import threading
import time
from datetime import datetime
from typing import Optional

from aiohttp import web

from config import Config
from camera import TapoCamera
from vision import ParkingVision
from state import ParkingState

logger = logging.getLogger(__name__)

# Module-level references populated by start_api()
_config: Optional[Config] = None
_camera: Optional[TapoCamera] = None
_vision: Optional[ParkingVision] = None
_state: Optional[ParkingState] = None
_start_time: float = 0.0


# ------------------------------------------------------------------
# Route handlers
# ------------------------------------------------------------------

async def handle_root(request: web.Request) -> web.Response:
    """GET / — Service health ping."""
    return web.json_response(
        {"status": "ok", "service": "parking-monitor", "version": "1.0.0"}
    )


async def handle_status_text(request: web.Request) -> web.Response:
    """GET /status — Plain text for Siri Shortcuts."""
    try:
        image_bytes = _camera.grab_frame()
        result = _vision.check_home_spot(image_bytes)
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
        logger.error("Error in /status handler: %s", exc)
        return web.Response(
            text=f"Error checking parking status: {exc}",
            status=500,
            content_type="text/plain",
        )


async def handle_status_json(request: web.Request) -> web.Response:
    """GET /status/json — Full JSON status."""
    try:
        image_bytes = _camera.grab_frame()
        result = _vision.check_home_spot(image_bytes)
        return web.json_response(
            {
                "status": result.get("status", "UNKNOWN"),
                "confidence": result.get("confidence", "low"),
                "description": result.get("description", ""),
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
        )
    except Exception as exc:
        logger.error("Error in /status/json handler: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


async def handle_scan_text(request: web.Request) -> web.Response:
    """GET /scan — Plain text scan result for Siri Shortcuts."""
    try:
        positions = _camera.scan_street()
        if not positions:
            return web.Response(
                text="Could not capture scan images.",
                content_type="text/plain",
                status=500,
            )

        free_positions = []
        for pos in positions:
            result = _vision.check_scan_position(pos["image"], pos["position_name"])
            if result.get("status") == "FREE":
                free_positions.append((pos, result))

        if free_positions:
            nearest = free_positions[0]
            pos_name = nearest[0]["position_name"]
            description = nearest[1].get("description", "")
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
        positions = _camera.scan_street()
        results = []
        for pos in positions:
            result = _vision.check_scan_position(pos["image"], pos["position_name"])
            results.append(
                {
                    "angle": pos["angle"],
                    "position_name": pos["position_name"],
                    "status": result.get("status", "UNKNOWN"),
                    "confidence": result.get("confidence", "low"),
                    "description": result.get("description", ""),
                }
            )
        return web.json_response({"positions": results, "timestamp": datetime.utcnow().isoformat() + "Z"})

    except Exception as exc:
        logger.error("Error in /scan/json handler: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


async def handle_snapshot(request: web.Request) -> web.Response:
    """GET /snapshot — Return current JPEG frame."""
    try:
        image_bytes = _camera.get_snapshot()
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
    # Check camera
    camera_ok = "ok"
    try:
        _camera.grab_frame()
    except Exception as exc:
        camera_ok = f"error: {exc}"

    # Check database
    db_ok = "ok"
    try:
        _state.get_current_status()
    except Exception as exc:
        db_ok = f"error: {exc}"

    uptime = int(time.time() - _start_time) if _start_time else 0

    return web.json_response(
        {
            "camera": camera_ok,
            "database": db_ok,
            "uptime_seconds": uptime,
        }
    )


# ------------------------------------------------------------------
# App factory
# ------------------------------------------------------------------

def _build_app() -> web.Application:
    """Create and configure the aiohttp Application."""
    app = web.Application()
    app.router.add_get("/", handle_root)
    app.router.add_get("/status", handle_status_text)
    app.router.add_get("/status/json", handle_status_json)
    app.router.add_get("/scan", handle_scan_text)
    app.router.add_get("/scan/json", handle_scan_json)
    app.router.add_get("/snapshot", handle_snapshot)
    app.router.add_get("/stats", handle_stats)
    app.router.add_get("/health", handle_health)
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
    global _config, _camera, _vision, _state, _start_time
    _config = cfg
    _camera = camera
    _vision = vision
    _state = state
    _start_time = time.time()

    logger.info("Starting HTTP API on %s:%d", cfg.API_HOST, cfg.API_PORT)
    app = _build_app()

    # Run inside a new event loop (this function runs in a thread)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    web.run_app(app, host=cfg.API_HOST, port=cfg.API_PORT, loop=loop, access_log=None)


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
