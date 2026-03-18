"""
calibrate.py — One-time camera calibration tool for Smart Parking Monitor.

Sweeps the camera through all pan angles, captures a frame at each position,
saves the images to calibration/, and generates an HTML review page.

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

SETTLE_TIME = 3.0  # seconds to wait after each move


def _print_banner() -> None:
    print("=" * 60)
    print("  Smart Parking Monitor — Camera Calibration Tool")
    print("=" * 60)
    print()
    print("This tool will:")
    print(f"  1. Sweep the camera through {len(CALIBRATION_ANGLES)} pan angles")
    print(f"     ({CALIBRATION_ANGLES[0]}° to {CALIBRATION_ANGLES[-1]}°)")
    print(f"  2. Save JPEG frames to ./{CALIBRATION_DIR}/")
    print("  3. Generate an HTML review page")
    print()
    print("Review the images to decide which angles to include in")
    print("SCAN_POSITIONS in your .env file.")
    print()


def _generate_html(image_files: list) -> str:
    """Generate an HTML index page for the captured calibration images."""
    items = ""
    for filename, angle in image_files:
        items += (
            f'    <div class="card">\n'
            f'      <img src="{filename}" alt="Angle {angle:+d}°">\n'
            f'      <p>Pan angle: <strong>{angle:+d}°</strong></p>\n'
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


def run_calibration() -> None:
    """Main calibration routine."""
    from config import load_config, validate
    from camera import TapoCamera

    _print_banner()
    input("Press ENTER to start calibration (Ctrl+C to cancel)… ")
    print()

    # Load config and connect camera
    config = load_config()
    if not validate(config):
        print("ERROR: Configuration invalid. Check your .env file.")
        sys.exit(1)

    camera = TapoCamera(config)
    try:
        camera.connect()
    except ConnectionError as exc:
        print(f"ERROR: Cannot connect to camera — {exc}")
        sys.exit(1)

    # Create output directory
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

                # Format filename, e.g. angle_-060.jpg, angle_+000.jpg
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

    # Generate HTML review page
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
    print("=" * 60)


if __name__ == "__main__":
    run_calibration()
