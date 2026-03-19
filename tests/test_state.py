"""
tests/test_state.py — Tests for state.py (uses in-memory SQLite)
"""

from datetime import datetime, timedelta, timezone

import pytest

from state import ParkingState


@pytest.fixture
def db():
    """Fresh in-memory database for each test."""
    s = ParkingState(":memory:")
    yield s
    s.close()


class TestRecordCheck:
    def test_record_and_retrieve(self, db):
        db.record_check("FREE", "high", "No cars visible", angle=0)
        result = db.get_current_status()
        assert result is not None
        assert result["status"] == "FREE"
        assert result["confidence"] == "high"
        assert result["description"] == "No cars visible"
        assert result["angle"] == 0

    def test_get_current_status_empty_db(self, db):
        assert db.get_current_status() is None

    def test_get_previous_status_empty(self, db):
        assert db.get_previous_status() is None

    def test_get_previous_status_returns_latest(self, db):
        db.record_check("FREE", "high", "Empty", angle=0)
        db.record_check("OCCUPIED", "medium", "Car arrived", angle=0)
        assert db.get_previous_status() == "OCCUPIED"

    def test_most_recent_check_returned(self, db):
        db.record_check("FREE", "high", "Empty", angle=0)
        db.record_check("OCCUPIED", "medium", "Car parked", angle=0)
        result = db.get_current_status()
        assert result["status"] == "OCCUPIED"


class TestHasStateChanged:
    def test_first_record_always_changed(self, db):
        assert db.has_state_changed("FREE") is True

    def test_same_status_not_changed(self, db):
        db.record_check("FREE", "high", "Empty", angle=0)
        assert db.has_state_changed("FREE") is False

    def test_different_status_is_changed(self, db):
        db.record_check("FREE", "high", "Empty", angle=0)
        assert db.has_state_changed("OCCUPIED") is True

    def test_unknown_to_free_is_changed(self, db):
        db.record_check("UNKNOWN", "low", "Unclear", angle=0)
        assert db.has_state_changed("FREE") is True


class TestGetStats:
    def test_empty_stats(self, db):
        stats = db.get_stats()
        assert stats["total_checks"] == 0
        assert stats["free_percentage"] == 0.0
        assert stats["occupied_percentage"] == 0.0

    def test_stats_percentages(self, db):
        db.record_check("FREE", "high", "Empty", angle=0)
        db.record_check("FREE", "high", "Empty", angle=0)
        db.record_check("FREE", "high", "Empty", angle=0)
        db.record_check("OCCUPIED", "medium", "Car", angle=0)
        stats = db.get_stats()
        assert stats["total_checks"] == 4
        assert stats["free_percentage"] == 75.0
        assert stats["occupied_percentage"] == 25.0

    def test_stats_checks_last_24h(self, db):
        db.record_check("FREE", "high", "Recent", angle=0)
        stats = db.get_stats()
        assert stats["checks_last_24h"] == 1

    def test_stats_last_check_present(self, db):
        db.record_check("FREE", "high", "Empty", angle=0)
        stats = db.get_stats()
        assert stats["last_check"] is not None
        assert stats["last_check"]["status"] == "FREE"

    def test_stats_busiest_and_freest_hours(self, db):
        db.record_check("OCCUPIED", "high", "Car", angle=0)
        db.record_check("FREE", "high", "Empty", angle=0)
        stats = db.get_stats()
        assert isinstance(stats["busiest_hours"], list)
        assert isinstance(stats["freest_hours"], list)


class TestGetHourlyBreakdown:
    def test_returns_24_entries(self, db):
        breakdown = db.get_hourly_breakdown()
        assert len(breakdown) == 24

    def test_all_hours_represented(self, db):
        breakdown = db.get_hourly_breakdown()
        hours = [row["hour"] for row in breakdown]
        assert hours == list(range(24))

    def test_empty_hours_have_zero_counts(self, db):
        breakdown = db.get_hourly_breakdown()
        for row in breakdown:
            assert row["total"] == 0
            assert row["free"] == 0
            assert row["occupied"] == 0


class TestCleanupOldRecords:
    def test_cleanup_removes_old_records(self, db):
        # Insert a record and then manually backdate it
        db.record_check("FREE", "high", "Old record", angle=0)
        old_ts = (datetime.now(timezone.utc) - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S")
        db._conn.execute("UPDATE checks SET timestamp = ?", (old_ts,))
        db._conn.commit()

        deleted = db.cleanup_old_records(days=90)
        assert deleted >= 1
        assert db.get_current_status() is None

    def test_cleanup_keeps_recent_records(self, db):
        db.record_check("FREE", "high", "Recent record", angle=0)
        deleted = db.cleanup_old_records(days=90)
        assert deleted == 0
        assert db.get_current_status() is not None

    def test_cleanup_returns_count(self, db):
        deleted = db.cleanup_old_records(days=90)
        assert deleted == 0  # no records to delete


class TestStateChanges:
    def test_record_state_change(self, db):
        db.record_state_change(None, "FREE", "First detection")
        db.record_state_change("FREE", "OCCUPIED", "Car arrived")
        stats = db.get_stats()
        assert stats["state_changes_last_24h"] == 2


class TestCalibrationMethods:
    def test_get_latest_calibration_empty(self, db):
        assert db.get_latest_calibration() is None

    def test_get_calibration_angles_empty(self, db):
        assert db.get_calibration_angles(999) == []

    def test_save_and_retrieve_calibration(self, db):
        # Create a mock CalibrationResult-like object
        class MockResult:
            timestamp = "2024-01-01 12:00:00"
            home_position = 0
            scan_positions = [-30, 0, 30]
            parking_side = "near"
            opposite_restriction = "double_yellow"
            street_description = "Clear view of street."
            safe_pan_min = -45
            safe_pan_max = 45
            angle_scores = [
                {
                    "angle": -30,
                    "street_visible": True,
                    "parking_area_visible": True,
                    "parking_side": "near",
                    "obstructions": ["none"],
                    "home_spot_visible": False,
                    "usefulness_score": 7,
                    "description": "Left section.",
                },
                {
                    "angle": 0,
                    "street_visible": True,
                    "parking_area_visible": True,
                    "parking_side": "near",
                    "obstructions": ["none"],
                    "home_spot_visible": True,
                    "usefulness_score": 9,
                    "description": "Centre — home spot visible.",
                },
            ]

        cal_id = db.save_calibration(MockResult())
        assert isinstance(cal_id, int)
        assert cal_id > 0

        cal = db.get_latest_calibration()
        assert cal is not None
        assert cal["home_position"] == 0
        assert cal["scan_positions"] == [-30, 0, 30]
        assert cal["parking_side"] == "near"
        assert cal["opposite_restriction"] == "double_yellow"
        assert cal["safe_pan_min"] == -45
        assert cal["safe_pan_max"] == 45

    def test_get_calibration_angles(self, db):
        class MockResult:
            timestamp = "2024-01-01 12:00:00"
            home_position = 0
            scan_positions = [0]
            parking_side = "near"
            opposite_restriction = "double_yellow"
            street_description = "Test."
            safe_pan_min = -180
            safe_pan_max = 180
            angle_scores = [
                {
                    "angle": -15,
                    "street_visible": True,
                    "parking_area_visible": False,
                    "parking_side": "near",
                    "obstructions": ["wall"],
                    "home_spot_visible": False,
                    "usefulness_score": 4,
                    "description": "Partial view.",
                },
                {
                    "angle": 0,
                    "street_visible": True,
                    "parking_area_visible": True,
                    "parking_side": "near",
                    "obstructions": ["none"],
                    "home_spot_visible": True,
                    "usefulness_score": 9,
                    "description": "Home spot.",
                },
            ]

        cal_id = db.save_calibration(MockResult())
        angles = db.get_calibration_angles(cal_id)
        assert len(angles) == 2
        # Should be ordered by angle
        assert angles[0]["angle"] == -15
        assert angles[1]["angle"] == 0
        assert angles[1]["home_spot"] == 1
        assert isinstance(angles[0]["obstructions"], list)

    def test_get_latest_calibration_returns_default_safe_bounds_when_null(self, db):
        """Rows with NULL safe_pan_min/max (from before migration) should default to ±180."""
        class MockResult:
            timestamp = "2024-01-01 12:00:00"
            home_position = 0
            scan_positions = [0]
            parking_side = "near"
            opposite_restriction = "double_yellow"
            street_description = "Test."
            safe_pan_min = -180
            safe_pan_max = 180
            angle_scores = []

        cal_id = db.save_calibration(MockResult())
        # Manually NULL the safe bounds to simulate a pre-migration row
        db._conn.execute(
            "UPDATE calibrations SET safe_pan_min = NULL, safe_pan_max = NULL WHERE id = ?",
            (cal_id,),
        )
        db._conn.commit()

        cal = db.get_latest_calibration()
        assert cal is not None
        assert cal["safe_pan_min"] == -180
        assert cal["safe_pan_max"] == 180
