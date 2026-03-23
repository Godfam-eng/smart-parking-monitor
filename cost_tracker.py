"""
cost_tracker.py — Claude API usage cost tracking for Smart Parking Monitor.

Records token usage and estimated costs per API call, and provides daily/
weekly/monthly cost summaries.
"""

import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Cost per million tokens (MTok) in USD
_COSTS = {
    # claude-sonnet-4-5 / claude-sonnet-*
    "sonnet": {"input": 3.00, "output": 15.00},
    # claude-haiku-3-5 / claude-haiku-*
    "haiku": {"input": 0.80, "output": 4.00},
    # Default fallback (Sonnet pricing)
    "default": {"input": 3.00, "output": 15.00},
}

# Typical image token count for a 720p JPEG processed by Claude
_IMAGE_TOKENS_ESTIMATE = 1600


def _model_to_key(model: str) -> str:
    """Map a model ID string to a cost key."""
    model_lower = model.lower()
    if "haiku" in model_lower:
        return "haiku"
    if "sonnet" in model_lower:
        return "sonnet"
    return "default"


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """
    Estimate the USD cost of a Claude API call.

    Args:
        model: Claude model ID string.
        input_tokens: Number of input tokens.
        output_tokens: Number of output tokens.

    Returns:
        Estimated cost in USD.
    """
    key = _model_to_key(model)
    rates = _COSTS[key]
    cost = (input_tokens / 1_000_000) * rates["input"] + (output_tokens / 1_000_000) * rates["output"]
    return round(cost, 8)


class CostTracker:
    """Tracks Claude API costs using the existing SQLite database."""

    def __init__(self, db_path: str) -> None:
        """
        Open the SQLite database and create the api_costs table if needed.

        Args:
            db_path: Filesystem path to the .db file, or ':memory:'.
        """
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_table()
        logger.info("CostTracker initialised with database: %s", db_path)

    def _create_table(self) -> None:
        """Create the api_costs table if it does not already exist."""
        with self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS api_costs (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       TEXT    DEFAULT CURRENT_TIMESTAMP,
                    model           TEXT    NOT NULL,
                    input_tokens    INTEGER NOT NULL,
                    output_tokens   INTEGER NOT NULL,
                    estimated_cost  REAL    NOT NULL,
                    check_type      TEXT    NOT NULL DEFAULT 'home'
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_costs_timestamp ON api_costs (timestamp)"
            )

    def record_call(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        check_type: str = "home",
    ) -> None:
        """
        Record a Claude API call and its estimated cost.

        Args:
            model: Claude model ID (e.g. "claude-haiku-4-5-20251001").
            input_tokens: Number of input tokens from the response.
            output_tokens: Number of output tokens from the response.
            check_type: One of "home", "scan", or "on_demand".
        """
        cost = _estimate_cost(model, input_tokens, output_tokens)
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO api_costs (model, input_tokens, output_tokens, estimated_cost, check_type)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (model, input_tokens, output_tokens, cost, check_type),
                )
        logger.debug(
            "CostTracker: model=%s in=%d out=%d cost=$%.6f type=%s",
            model, input_tokens, output_tokens, cost, check_type,
        )

    def get_daily_cost(self, date: Optional[datetime] = None) -> float:
        """
        Return total estimated cost for a given date.

        Args:
            date: Date to query (defaults to today UTC).

        Returns:
            Total cost in USD.
        """
        if date is None:
            date = datetime.now(timezone.utc)
        day_str = date.strftime("%Y-%m-%d")
        with self._lock:
            row = self._conn.execute(
                "SELECT SUM(estimated_cost) FROM api_costs WHERE date(timestamp) = ?",
                (day_str,),
            ).fetchone()
        return round(row[0] or 0.0, 6)

    def get_weekly_cost(self) -> float:
        """Return total estimated cost for the last 7 days (UTC)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            row = self._conn.execute(
                "SELECT SUM(estimated_cost) FROM api_costs WHERE timestamp >= ?",
                (cutoff,),
            ).fetchone()
        return round(row[0] or 0.0, 6)

    def get_monthly_cost(self) -> float:
        """Return total estimated cost for the last 30 days (UTC)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            row = self._conn.execute(
                "SELECT SUM(estimated_cost) FROM api_costs WHERE timestamp >= ?",
                (cutoff,),
            ).fetchone()
        return round(row[0] or 0.0, 6)

    def get_all_time_cost(self) -> float:
        """Return the all-time total estimated cost."""
        with self._lock:
            row = self._conn.execute(
                "SELECT SUM(estimated_cost) FROM api_costs"
            ).fetchone()
        return round(row[0] or 0.0, 6)

    def get_cost_summary(self) -> dict:
        """
        Return a summary dict with today/week/month/all_time costs.

        Returns:
            Dict with keys: today, week, month, all_time (all in USD float).
        """
        return {
            "today": self.get_daily_cost(),
            "week": self.get_weekly_cost(),
            "month": self.get_monthly_cost(),
            "all_time": self.get_all_time_cost(),
        }

    def get_total_calls(self) -> int:
        """Return the total number of API calls recorded."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM api_costs").fetchone()
        return row[0] or 0

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()
