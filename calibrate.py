"""
calibrate.py — Camera calibration tool for Smart Parking Monitor.

When an Anthropic API key is configured, uses Claude AI via AutoCalibrator to
score each angle and automatically suggest the best SCAN_POSITIONS.

Without an Anthropic key, falls back to a simple image-capture sweep that
saves JPEG files for manual review.

Usage:
    python calibrate.py
"""

import os
import sys
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

CALIBRATION_DIR = "calibration"

# Full range of angles to sweep
CALIBRATION_ANGLES = [-90, -75, -60, -45, -30, -15, 0, 15, 30, 45, 60, 75, 90]

SETTLE_TIME = 3.0  # seconds to wait after each move (image-only fallback)


def _print_banner() -> None:
    print("=" * 60)
    print("  Smart Parking Monitor — Camera Calibration Tool")
    print("=" * 60)
    print()
    print("This tool will:")
    print(f"  1. Sweep the camera through {len(CALIBRATION_ANGLES)} pan angles")
    print(f"     ({CALIBRATION_ANGLES[0]}° to {CALIBRATION_ANGLES[-1]}°)")
    print("  2. Score each angle using Claude AI (if API key configured)")
    print(f"  3. Save JPEG frames to ./{CALIBRATION_DIR}/")
    print("  4. Generate an HTML review page")
    print()
    print("The system will automatically suggest the best SCAN_POSITIONS.")
    print()


def _generate_html(image_files: list, angle_scores: dict | None = None, min_usefulness: int | None = None) -> str:
    """Generate an HTML index page for the captured calibration images."""
    items = ""
    for filename, angle in image_files:
        score_badge = ""
        if angle_scores and angle in angle_scores:
            s = angle_scores[angle]
            score = s.get("usefulness_score", "?")
            desc = s.get("description", "")
            threshold = min_usefulness if min_usefulness is not None else 6
            color = "#4CAF50" if isinstance(score, int) and score >= threshold else "#FF5722"
            score_badge = (
                f'<p style="color:{color}"><strong>Score: {score}/10</strong></p>'
                f"<p><em>{desc}</em></p>"
            )
        items += (
            f'    <div class="card">\n'
            f'      <img src="{filename}" alt="Angle {angle:+d}°">\n'
            f'      <p>Pan angle: <strong>{angle:+d}°</strong></p>\n'
            f"      {score_badge}\n"
            f'    </div>\n'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Parking Monitor — Calibration Images</title>
  <style>
    body {{
      font-family: sans-serif;
      background: #1a1a2e;
      color: #eee;
      margin: 0;
      padding: 20px;
    }}
    h1 {{ color: #4fc3f7; text-align: center; }}
    p.subtitle {{ text-align: center; color: #aaa; margin-bottom: 30px; }}
    .grid {{
      display: flex;
      flex-wrap: wrap;
      gap: 16px;
      justify-content: center;
    }}
    .card {{
      background: #16213e;
      border-radius: 8px;
      padding: 10px;
      text-align: center;
      width: 320px;
    }}
    .card img {{
      width: 100%;
      border-radius: 4px;
    }}
    .card p {{ margin: 8px 0 0; }}
  </style>
</head>
<body>
  <h1>🅿️ Parking Monitor — Calibration Images</h1>
  <p class="subtitle">Review each angle and note which ones show useful street coverage.</p>
  <div class="grid">
{items}
  </div>
  <p style="text-align:center; color:#777; margin-top:30px;">
    Update <code>SCAN_POSITIONS</code> in your <code>.env</code> file with the angles you want.
  </p>
</body>
</html>
"""


def _run_ai_calibration(config, camera) -> None:
    """Run full AI-assisted calibration using AutoCalibrator."""
    from vision import ParkingVision
    from state import ParkingState
    from auto_calibrate import AutoCalibrator

    vision = ParkingVision(config)
    state = ParkingState(config.DB_PATH)
    calibrator = AutoCalibrator(camera, vision, state)

    print()
    print("Running AI-assisted calibration (Claude will score each angle)…")
    print()

    result = calibrator.run_calibration()

    print()
    print("=" * 60)
    print("AI Calibration Results:")
    print(f"  Home position:  {result.home_position}°")
    print(f"  Scan positions: {', '.join(f'{p}°' for p in result.scan_positions)}")
    print(f"  Parking side:   {result.parking_side}")
    print(f"  Opposite side:  {result.opposite_restriction.replace('_', ' ')}")
    print()
    print("Angle scores:")
    for s in sorted(result.angle_scores, key=lambda x: x.get("angle", 0)):
        angle = s.get("angle", 0)
        score = s.get("usefulness_score", 0)
        desc = s.get("description", "")
        marker = " ← SELECTED" if angle in result.scan_positions else ""
        print(f"  {angle:+d}°: {score}/10  {desc}{marker}")
    print()

    # Also save images and generate HTML
    os.makedirs(CALIBRATION_DIR, exist_ok=True)
    image_files = []
    angle_score_map = {s.get("angle", 0): s for s in result.angle_scores}

    print("Saving images for HTML review page…")
    # Re-sweep to save images (calibrator already moved back to home)
    try:
        for angle in config.CALIBRATION_ANGLES:
            try:
                camera.move_to_angle(angle)
                image_bytes = camera.grab_frame()
                filename = f"angle_{angle:+04d}.jpg"
                filepath = os.path.join(CALIBRATION_DIR, filename)
                with open(filepath, "wb") as f:
                    f.write(image_bytes)
                image_files.append((filename, angle))
                print(f"  Saved: {filepath}")
            except Exception as exc:
                logger.warning("Could not save image at angle %+d°: %s", angle, exc)
    finally:
        try:
            camera.move_to_home()
        except Exception as exc:
            logger.warning("Could not return to home position: %s", exc)

    if image_files:
        html_path = os.path.join(CALIBRATION_DIR, "index.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(_generate_html(image_files, angle_score_map, config.CALIBRATION_MIN_USEFULNESS))
        print(f"Review page saved: {html_path}")

    print()
    print("=" * 60)
    print("Next steps:")
    print("  1. Open calibration/index.html in a browser")
    print("  2. The AI has already suggested optimal positions above")
    print(f"  3. Suggested SCAN_POSITIONS={','.join(str(p) for p in result.scan_positions)}")
    print(f"  4. Suggested HOME_POSITION={result.home_position}")
    print("  5. Update your .env file, or let auto-calibration handle it on next boot")
    print("=" * 60)

    state.close()


def _run_image_only_calibration(config, camera) -> None:
    """Original image-capture calibration (no AI scoring)."""
    os.makedirs(CALIBRATION_DIR, exist_ok=True)
    logger.info("Saving images to ./%s/", CALIBRATION_DIR)

    total = len(CALIBRATION_ANGLES)
    captured_files = []

    try:
        for n, angle in enumerate(CALIBRATION_ANGLES, start=1):
            print(f"Moving to angle {angle:+d}°… ({n}/{total})")
            try:
                camera.move_to_angle(angle)
                time.sleep(SETTLE_TIME)
                image_bytes = camera.grab_frame()
                filename = f"angle_{angle:+04d}.jpg"
                filepath = os.path.join(CALIBRATION_DIR, filename)
                with open(filepath, "wb") as f:
                    f.write(image_bytes)
                print(f"  Saved: {filepath}")
                captured_files.append((filename, angle))
            except Exception as exc:
                logger.error("Failed at angle %d: %s", angle, exc)
                print(f"  ERROR at angle {angle:+d}°: {exc} — skipping")
    finally:
        print()
        print("Returning camera to home position…")
        try:
            camera.move_to_home()
        except Exception as exc:
            logger.warning("Could not return to home position: %s", exc)

    if captured_files:
        html_path = os.path.join(CALIBRATION_DIR, "index.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(_generate_html(captured_files))
        print(f"Review page saved: {html_path}")

    print()
    print("=" * 60)
    print(f"Calibration complete: {len(captured_files)}/{total} images captured.")
    print()
    print("Next steps:")
    print(f"  1. Open {CALIBRATION_DIR}/index.html in a browser")
    print("  2. Note which angles give useful street coverage")
    print("  3. Update SCAN_POSITIONS in your .env file")
    print("     e.g. SCAN_POSITIONS=-60,-30,0,30,60")
    print()
    print("TIP: Set ANTHROPIC_API_KEY in .env to enable AI-assisted scoring.")
    print("=" * 60)


def run_calibration() -> None:
    """Main calibration routine — uses AI if API key is configured."""
    from config import load_config, validate
    from camera import TapoCamera

    _print_banner()
    input("Press ENTER to start calibration (Ctrl+C to cancel)… ")
    print()

    config = load_config()

    # Determine whether AI scoring is available
    has_ai = bool(config.ANTHROPIC_API_KEY)

    # Validate — require Anthropic key only if available (fall back gracefully)
    if not validate(config, require_telegram=False, require_anthropic=has_ai):
        print("ERROR: Configuration invalid. Check your .env file.")
        sys.exit(1)

    camera = TapoCamera(config)
    try:
        camera.connect()
    except ConnectionError as exc:
        print(f"ERROR: Cannot connect to camera — {exc}")
        sys.exit(1)

    if has_ai:
        print("✅ Claude API key found — using AI-assisted calibration")
        _run_ai_calibration(config, camera)
    else:
        print("ℹ️  No Claude API key — using image-only calibration (manual review)")
        _run_image_only_calibration(config, camera)


if __name__ == "__main__":
    run_calibration()
