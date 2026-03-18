"""
auto_calibrate.py — Smart auto-calibration engine for Smart Parking Monitor.

On first boot (or on-demand via Telegram /calibrate), sweeps the camera through
all configured angles, asks Claude to score each frame, then automatically selects
the best scan positions and home position.

Usage (standalone):
    python calibrate.py   # Uses AutoCalibrator internally

Typical usage from main.py:
    calibrator = AutoCalibrator(camera, vision, state, notifications)
    if calibrator.needs_calibration():
        result = calibrator.run_calibration()
        config.HOME_POSITION = result.home_position
        config.SCAN_POSITIONS = result.scan_positions
"""

import dataclasses
import logging
import threading
from datetime import datetime
from typing import List, Optional

from config import Config
from camera import TapoCamera
from vision import ParkingVision
from state import ParkingState
from notifications import NotificationManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class CalibrationResult:
    """Encapsulates the output of a full auto-calibration sweep."""

    timestamp: str
    home_position: int
    scan_positions: List[int]
    parking_side: str           # "near", "far", or "both"
    opposite_restriction: str   # "double_yellow", "single_yellow", "none", "unclear"
    angle_scores: List[dict]    # Claude's full assessment dict per angle
    street_description: str     # Description from the highest-scoring angle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _majority_vote(items: list, default: str) -> str:
    """Return the most common value in *items*, or *default* if the list is empty."""
    if not items:
        return default
    counts: dict = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    return max(counts, key=lambda k: counts[k])


# ---------------------------------------------------------------------------
# AutoCalibrator
# ---------------------------------------------------------------------------

class AutoCalibrator:
    """
    Sweeps the camera across all configured calibration angles, asks Claude to
    score each frame, and automatically selects optimal scan positions.

    Thread safety: ``run_calibration()`` acquires the camera's internal RLock
    for the entire sweep so that the monitoring loop cannot interleave camera
    movements with calibration frames.
    """

    def __init__(
        self,
        camera: TapoCamera,
        vision: ParkingVision,
        state: ParkingState,
        notifications: Optional[NotificationManager] = None,
    ) -> None:
        self.camera = camera
        self.vision = vision
        self.state = state
        self.notifications = notifications
        self.config: Config = camera.config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_calibration(self) -> CalibrationResult:
        """
        Run a full auto-calibration sweep.

        Phase 1 — sweeps all CALIBRATION_ANGLES.
        Phase 2 — Claude scores each frame.
        Phase 3 — selects scan positions (score ≥ CALIBRATION_MIN_USEFULNESS).
        Phase 4 — saves result to the database.
        Phase 5 — sends Telegram progress messages if notifications available.

        Returns:
            CalibrationResult with the selected home_position and scan_positions.
        """
        angles = self.config.CALIBRATION_ANGLES
        total = len(angles)
        angle_scores: List[dict] = []

        logger.info("Starting auto-calibration sweep: %d angles", total)
        self._notify(f"🔧 Auto-calibration starting... sweeping {total} angles")

        # Hold the camera lock for the entire sweep to prevent the monitoring
        # loop from interleaving camera moves during calibration.
        with self.camera._lock:
            try:
                for i, angle in enumerate(angles, start=1):
                    score_dict = self._sweep_one_angle(angle, i, total)
                    angle_scores.append(score_dict)
            finally:
                try:
                    self.camera.move_to_home()
                except Exception as exc:
                    logger.error("Failed to return to home after calibration: %s", exc)

        result = self._select_positions(angle_scores)
        self.state.save_calibration(result)
        self._send_final_summary(result, angle_scores)
        logger.info(
            "Calibration complete: home=%d, positions=%s",
            result.home_position,
            result.scan_positions,
        )
        return result

    def get_current_calibration(self) -> Optional[CalibrationResult]:
        """
        Load the most recent calibration from the database.

        Returns:
            CalibrationResult if a calibration exists, otherwise None.
        """
        cal_data = self.state.get_latest_calibration()
        if cal_data is None:
            return None

        angle_scores = self.state.get_calibration_angles(cal_data["id"])

        return CalibrationResult(
            timestamp=cal_data.get("timestamp", ""),
            home_position=cal_data["home_position"],
            scan_positions=cal_data.get("scan_positions", []),
            parking_side=cal_data.get("parking_side", "near"),
            opposite_restriction=cal_data.get("opposite_restriction", "double_yellow"),
            angle_scores=angle_scores,
            street_description=cal_data.get("street_description", ""),
        )

    def needs_calibration(self) -> bool:
        """
        Return True if no calibration exists or the existing one is stale.

        Staleness is determined by CALIBRATION_INTERVAL_DAYS:
        - 0  → never auto-recalibrate (only on first boot / manual /calibrate)
        - >0 → recalibrate when calibration is older than N days
        """
        if not self.config.AUTO_CALIBRATE:
            return False

        latest = self.state.get_latest_calibration()
        if latest is None:
            logger.info("No calibration found — calibration required")
            return True

        if self.config.CALIBRATION_INTERVAL_DAYS <= 0:
            return False  # Only calibrate on first boot

        timestamp = latest.get("timestamp", "")
        try:
            cal_time = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
            age_days = (datetime.now() - cal_time).days
            if age_days >= self.config.CALIBRATION_INTERVAL_DAYS:
                logger.info(
                    "Calibration is %d days old (interval=%d days) — recalibration required",
                    age_days,
                    self.config.CALIBRATION_INTERVAL_DAYS,
                )
                return True
        except ValueError:
            logger.warning("Cannot parse calibration timestamp '%s' — recalibrating", timestamp)
            return True

        return False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _sweep_one_angle(self, angle: int, index: int, total: int) -> dict:
        """Move to *angle*, grab a frame, ask Claude to score it, and return the score dict."""
        try:
            # move_to_angle and grab_frame are RLock-safe (reentrant) so they work
            # fine while the outer _lock is held by this thread.
            self.camera.move_to_angle(angle)
            image_bytes = self.camera.grab_frame()
            assessment = self.vision.assess_calibration_frame(image_bytes, angle)
            assessment["angle"] = angle

            score = assessment.get("usefulness_score", 0)
            emoji = "✅" if score >= self.config.CALIBRATION_MIN_USEFULNESS else "❌"
            obstructions = assessment.get("obstructions", [])
            obstruct_str = ""
            if obstructions and obstructions != ["none"]:
                obstruct_str = f" ({', '.join(o.replace('_', ' ') for o in obstructions)})"
            msg = (
                f"📸 Scanning angle {angle:+d}° ({index}/{total}) "
                f"— usefulness: {score}/10 {emoji}{obstruct_str}"
            )
            logger.info(msg)
            self._notify(msg, image=image_bytes)

        except Exception as exc:
            logger.error("Failed to sweep angle %+d°: %s", angle, exc)
            assessment = {
                "angle": angle,
                "street_visible": False,
                "parking_area_visible": False,
                "parking_side": "none",
                "opposite_restriction": "unclear",
                "obstructions": ["none"],
                "home_spot_visible": False,
                "usefulness_score": 0,
                "description": f"Error: {exc}",
            }
            self._notify(f"⚠️ Angle {angle:+d}° ({index}/{total}) — error: {exc}")

        return assessment

    def _select_positions(self, angle_scores: List[dict]) -> CalibrationResult:
        """Derive home_position, scan_positions, parking_side, etc. from Claude's scores."""
        min_score = self.config.CALIBRATION_MIN_USEFULNESS

        # Scan positions: all angles at or above the minimum usefulness threshold
        useful = [s for s in angle_scores if s.get("usefulness_score", 0) >= min_score]
        scan_positions = sorted(s["angle"] for s in useful)
        if not scan_positions:
            logger.warning(
                "No angles scored ≥ %d — falling back to config defaults", min_score
            )
            scan_positions = list(self.config.SCAN_POSITIONS)

        # Home position: angle where home_spot_visible=True, closest to 0°
        home_candidates = [s for s in angle_scores if s.get("home_spot_visible", False)]
        if home_candidates:
            home_position = min(home_candidates, key=lambda s: abs(s["angle"]))["angle"]
        else:
            home_position = self.config.HOME_POSITION

        # Parking side: majority consensus from non-"none" responses
        parking_sides = [
            s.get("parking_side", "")
            for s in angle_scores
            if s.get("parking_side") not in (None, "none", "")
        ]
        parking_side = _majority_vote(parking_sides, default="near")

        # Opposite restriction: majority consensus from non-"unclear" responses
        restrictions = [
            s.get("opposite_restriction", "")
            for s in angle_scores
            if s.get("opposite_restriction") not in (None, "unclear", "")
        ]
        opposite_restriction = _majority_vote(restrictions, default="double_yellow")

        # Street description: from the highest-scoring angle
        if angle_scores:
            best = max(angle_scores, key=lambda s: s.get("usefulness_score", 0))
            street_description = best.get("description", "")
        else:
            street_description = ""

        return CalibrationResult(
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            home_position=home_position,
            scan_positions=scan_positions,
            parking_side=parking_side,
            opposite_restriction=opposite_restriction,
            angle_scores=angle_scores,
            street_description=street_description,
        )

    def _send_final_summary(self, result: CalibrationResult, angle_scores: List[dict]) -> None:
        """Send the calibration summary to Telegram."""
        if not self.notifications:
            return

        scores_lines = "\n".join(
            f"  {s['angle']:+d}°: {s.get('usefulness_score', 0)}/10"
            for s in sorted(angle_scores, key=lambda x: x.get("angle", 0))
        )
        restriction_label = result.opposite_restriction.replace("_", " ")
        scan_str = ", ".join(f"{p}°" for p in result.scan_positions)

        summary = (
            "✅ Calibration complete!\n\n"
            f"🏠 Home position: {result.home_position}°\n"
            f"🔍 Scan positions: {scan_str}\n"
            f"🅿️ Parking side: {result.parking_side}\n"
            f"🟡 Opposite side: {restriction_label}\n\n"
            f"📊 Scores:\n{scores_lines}"
        )
        self._notify(summary)

    def _notify(self, message: str, image: Optional[bytes] = None) -> None:
        """Send a Telegram message if notifications are configured."""
        if self.notifications:
            try:
                self.notifications.send_telegram(message=message, image=image)
            except Exception as exc:
                logger.warning("Failed to send calibration notification: %s", exc)
