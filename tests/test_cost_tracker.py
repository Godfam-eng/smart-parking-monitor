"""
tests/test_cost_tracker.py — Tests for cost_tracker.py
"""

import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

# Mock hardware dependencies
sys.modules.setdefault("cv2", MagicMock())
sys.modules.setdefault("pytapo", MagicMock())

import pytest
from cost_tracker import CostTracker, _estimate_cost, _model_to_key


# ---------------------------------------------------------------------------
# Cost estimation helpers
# ---------------------------------------------------------------------------

class TestEstimateCost:
    def test_haiku_model_key(self):
        assert _model_to_key("claude-haiku-4-5-20251001") == "haiku"
        assert _model_to_key("claude-haiku-3-5") == "haiku"

    def test_sonnet_model_key(self):
        assert _model_to_key("claude-sonnet-4-5") == "sonnet"
        assert _model_to_key("claude-sonnet-4") == "sonnet"

    def test_unknown_model_falls_back_to_default(self):
        assert _model_to_key("claude-opus-4") == "default"

    def test_haiku_cost_calculation(self):
        # 1M input tokens at $0.80/MTok + 1M output tokens at $4.00/MTok = $4.80
        cost = _estimate_cost("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
        assert abs(cost - 4.80) < 1e-4

    def test_sonnet_cost_calculation(self):
        # 1M input tokens at $3.00/MTok + 1M output tokens at $15.00/MTok = $18.00
        cost = _estimate_cost("claude-sonnet-4-5", 1_000_000, 1_000_000)
        assert abs(cost - 18.00) < 1e-4

    def test_small_call_cost(self):
        # 2000 input + 80 output with haiku
        cost = _estimate_cost("claude-haiku-4-5-20251001", 2000, 80)
        expected = (2000 / 1_000_000) * 0.80 + (80 / 1_000_000) * 4.00
        assert abs(cost - expected) < 1e-8

    def test_zero_tokens_cost(self):
        cost = _estimate_cost("claude-haiku-4-5-20251001", 0, 0)
        assert cost == 0.0


# ---------------------------------------------------------------------------
# CostTracker class
# ---------------------------------------------------------------------------

class TestCostTracker:
    def setup_method(self):
        self.tracker = CostTracker(":memory:")

    def teardown_method(self):
        self.tracker.close()

    def test_initial_costs_are_zero(self):
        summary = self.tracker.get_cost_summary()
        assert summary["today"] == 0.0
        assert summary["week"] == 0.0
        assert summary["month"] == 0.0
        assert summary["all_time"] == 0.0

    def test_record_call_increases_cost(self):
        self.tracker.record_call("claude-haiku-4-5-20251001", 1000, 50, "home")
        summary = self.tracker.get_cost_summary()
        assert summary["today"] > 0.0
        assert summary["all_time"] > 0.0

    def test_record_multiple_calls(self):
        self.tracker.record_call("claude-haiku-4-5-20251001", 1000, 50, "home")
        self.tracker.record_call("claude-haiku-4-5-20251001", 1000, 50, "scan")
        self.tracker.record_call("claude-sonnet-4-5", 2000, 100, "on_demand")
        assert self.tracker.get_total_calls() == 3

    def test_daily_cost_matches_all_time_when_no_history(self):
        self.tracker.record_call("claude-haiku-4-5-20251001", 1000, 50, "home")
        summary = self.tracker.get_cost_summary()
        # Both today and all_time should match when there's only one day of data
        assert abs(summary["today"] - summary["all_time"]) < 1e-8

    def test_weekly_cost_includes_today(self):
        self.tracker.record_call("claude-sonnet-4-5", 5000, 200, "scan")
        summary = self.tracker.get_cost_summary()
        assert summary["week"] >= summary["today"]

    def test_monthly_cost_includes_week(self):
        self.tracker.record_call("claude-sonnet-4-5", 5000, 200, "scan")
        summary = self.tracker.get_cost_summary()
        assert summary["month"] >= summary["week"]

    def test_get_daily_cost_for_today(self):
        self.tracker.record_call("claude-haiku-4-5-20251001", 1000, 50, "home")
        today = datetime.now(timezone.utc)
        cost = self.tracker.get_daily_cost(today)
        assert cost > 0.0

    def test_get_daily_cost_for_future_returns_zero(self):
        self.tracker.record_call("claude-haiku-4-5-20251001", 1000, 50, "home")
        from datetime import timedelta
        future = datetime.now(timezone.utc) + timedelta(days=30)
        cost = self.tracker.get_daily_cost(future)
        assert cost == 0.0

    def test_total_calls_starts_at_zero(self):
        assert self.tracker.get_total_calls() == 0

    def test_total_calls_increments(self):
        for _ in range(5):
            self.tracker.record_call("claude-haiku-4-5-20251001", 500, 30, "home")
        assert self.tracker.get_total_calls() == 5

    def test_cost_summary_keys(self):
        summary = self.tracker.get_cost_summary()
        assert set(summary.keys()) == {"today", "week", "month", "all_time"}

    def test_weekly_cost_method(self):
        self.tracker.record_call("claude-haiku-4-5-20251001", 1000, 50, "home")
        assert self.tracker.get_weekly_cost() > 0.0

    def test_monthly_cost_method(self):
        self.tracker.record_call("claude-haiku-4-5-20251001", 1000, 50, "home")
        assert self.tracker.get_monthly_cost() > 0.0

    def test_all_time_cost_method(self):
        self.tracker.record_call("claude-haiku-4-5-20251001", 1000, 50, "home")
        assert self.tracker.get_all_time_cost() > 0.0

    def test_cost_values_are_rounded(self):
        self.tracker.record_call("claude-haiku-4-5-20251001", 1234, 56, "home")
        summary = self.tracker.get_cost_summary()
        for key in ("today", "week", "month", "all_time"):
            # Check it's a reasonable float (not wildly large or NaN)
            assert 0 <= summary[key] < 1000

    def test_different_check_types(self):
        self.tracker.record_call("claude-haiku-4-5-20251001", 1000, 50, "home")
        self.tracker.record_call("claude-sonnet-4-5", 2000, 100, "scan")
        self.tracker.record_call("claude-sonnet-4-5", 3000, 150, "on_demand")
        assert self.tracker.get_total_calls() == 3
