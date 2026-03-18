"""
api.py — Tiny HTTP server for Siri Shortcut integration.

Endpoints:
    GET /status       — plain text sentence for Siri to speak
    GET /status/json  — full JSON response
    GET /scan         — runs a full street scan and returns spoken result

Usage:
    python api.py
"""

import asyncio
import logging
import threading

from flask import Flask, jsonify

import config
import camera
import vision
import state
import notifications

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)


def _quick_status() -> dict:
    """Grab a snapshot, run home-spot analysis and return a result dict."""
    try:
        image = camera.get_snapshot()
    except RuntimeError as exc:
        return {"status": "ERROR", "confidence": "low", "description": str(exc)}
    result = vision.check_home_spot(image)
    return {
        "status": result.status,
        "confidence": result.confidence,
        "description": result.description,
    }


@app.route("/status", methods=["GET"])
def route_status_text():
    """Return a plain text sentence suitable for Siri to speak."""
    data = _quick_status()
    if data["status"] == "FREE":
        text = f"Your parking spot is free. {data['description']}"
    elif data["status"] == "ERROR":
        text = f"Camera error: {data['description']}"
    else:
        text = f"Your parking spot is taken. {data['description']}"
    return text, 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/status/json", methods=["GET"])
def route_status_json():
    """Return full JSON response."""
    data = _quick_status()
    return jsonify(data)


@app.route("/scan", methods=["GET"])
def route_scan():
    """Run a full street scan and return a spoken result sentence."""

    async def do_scan():
        positions = await camera.scan_street()
        found = False
        label = None
        description = ""
        for pos in positions:
            if pos["image"] is None:
                continue
            result = vision.check_scan_position(pos["image"], pos["label"])
            if result.status == "FREE" and vision.is_confident_enough(result):
                found = True
                label = pos["label"]
                description = result.description
                break
        return notifications.send_scan_result(found, label, description)

    # Run the async scan in a new event loop on a background thread so Flask
    # can call it synchronously.
    result_holder: list[str] = []

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result_holder.append(loop.run_until_complete(do_scan()))
        loop.close()

    t = threading.Thread(target=run)
    t.start()
    t.join(timeout=60)

    text = result_holder[0] if result_holder else "Scan timed out."
    return text, 200, {"Content-Type": "text/plain; charset=utf-8"}


if __name__ == "__main__":
    state.init_db()
    logger.info(
        "API server starting on %s:%d", config.API_HOST, config.API_PORT
    )
    app.run(host=config.API_HOST, port=config.API_PORT, debug=False)
