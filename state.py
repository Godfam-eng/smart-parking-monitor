"""
state.py — SQLite-backed state and history manager for Smart Parking Monitor.

Stores all parking checks, state changes, and provides statistics queries.
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from auto_calibrate import CalibrationResult

logger = logging.getLogger(__name__)


class ParkingState:
    """Manages parking history and current state in a SQLite database."""

    def __init__(self, db_path: str) -> None:
        """
        Open (or create) the SQLite database and initialise the schema.

        Args:
            db_path: Filesystem path to the .db file, or ':memory:' for in-memory.
        """
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        self._migrate_schema()
        logger.info("ParkingState initialised with database: %s", db_path)

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _create_tables(self) -> None:
        """Create database tables if they do not already exist."""
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS checks (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    DEFAULT CURRENT_TIMESTAMP,
                    status      TEXT    NOT NULL,
                    confidence  TEXT    NOT NULL,
                    description TEXT,
                    angle       INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS state_changes (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    DEFAULT CURRENT_TIMESTAMP,
                    old_status  TEXT,
                    new_status  TEXT    NOT NULL,
                    description TEXT
                );

                CREATE TABLE IF NOT EXISTS calibrations (
                    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp            TEXT    DEFAULT CURRENT_TIMESTAMP,
                    home_position        INTEGER NOT NULL,
                    scan_positions       TEXT    NOT NULL,
                    parking_side         TEXT    NOT NULL DEFAULT 'near',
                    opposite_restriction TEXT    NOT NULL DEFAULT 'double_yellow',
                    street_description   TEXT,
                    angle_count          INTEGER DEFAULT 0,
                    safe_pan_min         INTEGER DEFAULT -180,
                    safe_pan_max         INTEGER DEFAULT 180
                );

                CREATE TABLE IF NOT EXISTS calibration_angles (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    calibration_id   INTEGER NOT NULL,
                    angle            INTEGER NOT NULL,
                    street_visible   INTEGER DEFAULT 0,
                    parking_visible  INTEGER DEFAULT 0,
                    parking_side     TEXT,
                    obstructions     TEXT,
                    home_spot        INTEGER DEFAULT 0,
                    usefulness       INTEGER DEFAULT 0,
                    description      TEXT,
                    FOREIGN KEY (calibration_id) REFERENCES calibrations(id)
                );

                CREATE INDEX IF NOT EXISTS idx_checks_timestamp
                    ON checks (timestamp);

                CREATE INDEX IF NOT EXISTS idx_state_changes_timestamp
                    ON state_changes (timestamp);

                CREATE TABLE IF NOT EXISTS watch_mode (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    mode        TEXT    NOT NULL,
                    eta_minutes INTEGER DEFAULT 0,
                    started_at  TEXT    NOT NULL,
                    expires_at  TEXT    NOT NULL,
                    chat_id     TEXT    DEFAULT '',
                    active      INTEGER DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS scan_cache (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    DEFAULT CURRENT_TIMESTAMP,
                    positions   TEXT    NOT NULL,
                    summary     TEXT    DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_scan_cache_timestamp
                    ON scan_cache (timestamp);

                CREATE TABLE IF NOT EXISTS api_costs (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       TEXT    DEFAULT CURRENT_TIMESTAMP,
                    model           TEXT    NOT NULL,
                    input_tokens    INTEGER NOT NULL,
                    output_tokens   INTEGER NOT NULL,
                    estimated_cost  REAL    NOT NULL,
                    check_type      TEXT    NOT NULL DEFAULT 'home'
                );

                CREATE INDEX IF NOT EXISTS idx_api_costs_timestamp
                    ON api_costs (timestamp);

                CREATE TABLE IF NOT EXISTS transient_flips (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT    DEFAULT CURRENT_TIMESTAMP,
                    from_status TEXT    NOT NULL,
                    to_status   TEXT    NOT NULL,
                    back_status TEXT    NOT NULL,
                    description TEXT
                );
                """
            )

    def _migrate_schema(self) -> None:
        """Apply incremental schema migrations for backwards compatibility.

        Adds columns that were introduced after the initial schema so that
        existing databases are upgraded automatically on startup.
        """
        # Each entry is (table, column, column_definition).  Values are
        # hardcoded literals — never user-supplied — so SQL construction is safe.
        migrations = [
            ("calibrations", "safe_pan_min", "INTEGER DEFAULT -180"),
            ("calibrations", "safe_pan_max", "INTEGER DEFAULT 180"),
        ]
        with self._conn:
            for table, column, column_def in migrations:
                # Validate that table/column are safe identifiers (letters, digits, _)
                # before embedding them in the ALTER TABLE statement.
                if not table.replace("_", "").isalnum() or not column.replace("_", "").isalnum():
                    logger.warning(
                        "Skipping migration: unsafe identifier table=%r column=%r",
                        table, column,
                    )
                    continue
                try:
                    self._conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {column_def}"  # noqa: S608
                    )
                    logger.info("Schema migration: added column %s.%s", table, column)
                except Exception:
                    # Column already exists — safe to ignore
                    pass

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def record_check(
        self,
        status: str,
        confidence: str,
        description: str,
        angle: int = 0,
    ) -> None:
        """
        Insert a new parking check record.

        Args:
            status: "FREE", "OCCUPIED", or "UNKNOWN".
            confidence: "high", "medium", or "low".
            description: Human-readable description from vision analysis.
            angle: Pan angle at which the frame was captured.
        """
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO checks (status, confidence, description, angle) VALUES (?, ?, ?, ?)",
                    (status, confidence, description, angle),
                )
        logger.debug("Recorded check: status=%s confidence=%s", status, confidence)

    def record_state_change(
        self,
        old_status: Optional[str],
        new_status: str,
        description: str,
    ) -> None:
        """
        Record a parking state transition (e.g. FREE → OCCUPIED).

        Args:
            old_status: Previous status string, or None if first record.
            new_status: New status string.
            description: Human-readable description of the change.
        """
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO state_changes (old_status, new_status, description) VALUES (?, ?, ?)",
                    (old_status, new_status, description),
                )
        logger.info("State changed: %s → %s", old_status, new_status)

    def record_transient_flip(
        self,
        from_status: str,
        to_status: str,
        back_status: str,
        description: str = "",
    ) -> None:
        """
        Record a transient state flip that was suppressed by notification batching.

        Args:
            from_status: Status before the flip.
            to_status:   Status during the flip (the one that was pending).
            back_status: Status that cancelled the notification.
            description: Optional description.
        """
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO transient_flips (from_status, to_status, back_status, description)"
                    " VALUES (?, ?, ?, ?)",
                    (from_status, to_status, back_status, description),
                )
        logger.info(
            "Transient flip recorded: %s → %s → %s", from_status, to_status, back_status
        )

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_current_status(self) -> Optional[dict]:
        """
        Return the most recent check record as a dict, or None if no records exist.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM checks ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def get_previous_status(self) -> Optional[str]:
        """Return just the status string from the most recent check, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM checks ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return row["status"] if row else None

    def has_state_changed(self, new_status: str) -> bool:
        """
        Return True if *new_status* differs from the previous recorded status,
        or if there is no previous record.
        """
        previous = self.get_previous_status()
        if previous is None:
            return True
        return previous != new_status

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """
        Return comprehensive statistics about parking history.

        Returns:
            Dict containing total_checks, free_percentage, occupied_percentage,
            busiest_hours, freest_hours, checks_last_24h, state_changes_last_24h,
            last_check, days_of_data.
        """
        with self._lock:
            cursor = self._conn

            total_checks = cursor.execute(
                "SELECT COUNT(*) FROM checks"
            ).fetchone()[0]

            free_count = cursor.execute(
                "SELECT COUNT(*) FROM checks WHERE status='FREE'"
            ).fetchone()[0]

            occupied_count = cursor.execute(
                "SELECT COUNT(*) FROM checks WHERE status='OCCUPIED'"
            ).fetchone()[0]

            free_pct = round(free_count / total_checks * 100, 1) if total_checks else 0.0
            occupied_pct = round(occupied_count / total_checks * 100, 1) if total_checks else 0.0

            # Busiest hours (most OCCUPIED checks)
            busiest = cursor.execute(
                """
                SELECT strftime('%H', timestamp) AS hour, COUNT(*) AS cnt
                FROM checks
                WHERE status = 'OCCUPIED'
                GROUP BY hour
                ORDER BY cnt DESC
                LIMIT 5
                """
            ).fetchall()

            # Freest hours (most FREE checks)
            freest = cursor.execute(
                """
                SELECT strftime('%H', timestamp) AS hour, COUNT(*) AS cnt
                FROM checks
                WHERE status = 'FREE'
                GROUP BY hour
                ORDER BY cnt DESC
                LIMIT 5
                """
            ).fetchall()

            since_24h = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")

            checks_24h = cursor.execute(
                "SELECT COUNT(*) FROM checks WHERE timestamp >= ?", (since_24h,)
            ).fetchone()[0]

            changes_24h = cursor.execute(
                "SELECT COUNT(*) FROM state_changes WHERE timestamp >= ?", (since_24h,)
            ).fetchone()[0]

            last_check_row = cursor.execute(
                "SELECT timestamp, status FROM checks ORDER BY id DESC LIMIT 1"
            ).fetchone()

            last_check = dict(last_check_row) if last_check_row else None

            # Days of data
            first_row = cursor.execute(
                "SELECT timestamp FROM checks ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if first_row:
                try:
                    # SQLite stores timestamps as naive UTC strings; strip tz info for comparison
                    first_dt = datetime.strptime(first_row["timestamp"], "%Y-%m-%d %H:%M:%S")
                    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
                    days_of_data = (now_naive - first_dt).days
                except ValueError:
                    days_of_data = 0
            else:
                days_of_data = 0

        return {
            "total_checks": total_checks,
            "free_percentage": free_pct,
            "occupied_percentage": occupied_pct,
            "busiest_hours": [{"hour": int(r["hour"]), "count": r["cnt"]} for r in busiest],
            "freest_hours": [{"hour": int(r["hour"]), "count": r["cnt"]} for r in freest],
            "checks_last_24h": checks_24h,
            "state_changes_last_24h": changes_24h,
            "last_check": last_check,
            "days_of_data": days_of_data,
        }

    def get_hourly_breakdown(self) -> list:
        """
        Return per-hour statistics (0–23) showing free vs occupied counts.

        Returns:
            List of 24 dicts, one per hour:
            {"hour": int, "total": int, "free": int, "occupied": int, "free_percentage": float}
        """
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    strftime('%H', timestamp) AS hour,
                    COUNT(*) AS total,
                    SUM(CASE WHEN status='FREE' THEN 1 ELSE 0 END) AS free,
                    SUM(CASE WHEN status='OCCUPIED' THEN 1 ELSE 0 END) AS occupied
                FROM checks
                GROUP BY hour
                ORDER BY hour
                """
            ).fetchall()

        # Build a dict keyed by hour
        by_hour = {int(r["hour"]): r for r in rows}

        result = []
        for h in range(24):
            if h in by_hour:
                r = by_hour[h]
                total = r["total"]
                free = r["free"] or 0
                occupied = r["occupied"] or 0
                free_pct = round(free / total * 100, 1) if total else 0.0
            else:
                total = free = occupied = 0
                free_pct = 0.0

            result.append(
                {
                    "hour": h,
                    "total": total,
                    "free": free,
                    "occupied": occupied,
                    "free_percentage": free_pct,
                }
            )

        return result

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------

    def save_calibration(self, result: "CalibrationResult") -> int:
        """
        Save a calibration result to the database.

        Args:
            result: CalibrationResult dataclass from auto_calibrate.py.

        Returns:
            The integer ID of the newly inserted calibration row.
        """
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    """
                    INSERT INTO calibrations (
                        timestamp, home_position, scan_positions, parking_side,
                        opposite_restriction, street_description, angle_count,
                        safe_pan_min, safe_pan_max
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result.timestamp,
                        result.home_position,
                        json.dumps(result.scan_positions),
                        result.parking_side,
                        result.opposite_restriction,
                        result.street_description,
                        len(result.angle_scores),
                        getattr(result, "safe_pan_min", -180),
                        getattr(result, "safe_pan_max", 180),
                    ),
                )
                calibration_id = cur.lastrowid

                for score in result.angle_scores:
                    self._conn.execute(
                        """
                        INSERT INTO calibration_angles (
                            calibration_id, angle, street_visible, parking_visible,
                            parking_side, obstructions, home_spot, usefulness, description
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            calibration_id,
                            score.get("angle", 0),
                            int(bool(score.get("street_visible", False))),
                            int(bool(score.get("parking_area_visible", False))),
                            score.get("parking_side", "none"),
                            json.dumps(score.get("obstructions", [])),
                            int(bool(score.get("home_spot_visible", False))),
                            score.get("usefulness_score", 0),
                            score.get("description", ""),
                        ),
                    )

        logger.info(
            "Saved calibration ID=%d: home=%d, positions=%s, safe_pan=[%d°, %d°]",
            calibration_id,
            result.home_position,
            result.scan_positions,
            getattr(result, "safe_pan_min", -180),
            getattr(result, "safe_pan_max", 180),
        )
        return calibration_id

    def get_latest_calibration(self) -> Optional[dict]:
        """
        Return the most recent calibration record as a dict, or None if none exist.

        The ``scan_positions`` field is decoded from JSON to a list of ints.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM calibrations ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        try:
            data["scan_positions"] = json.loads(data.get("scan_positions", "[]"))
        except (json.JSONDecodeError, TypeError):
            data["scan_positions"] = []
        # Provide defaults for columns added by migration (older rows may be NULL)
        if data.get("safe_pan_min") is None:
            data["safe_pan_min"] = -180
        if data.get("safe_pan_max") is None:
            data["safe_pan_max"] = 180
        return data

    def get_calibration_angles(self, calibration_id: int) -> list:
        """
        Return all per-angle scores for a given calibration run.

        Args:
            calibration_id: The ID of the calibration to look up.

        Returns:
            List of dicts, one per angle, ordered by angle.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM calibration_angles WHERE calibration_id = ? ORDER BY angle",
                (calibration_id,),
            ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            try:
                d["obstructions"] = json.loads(d.get("obstructions", "[]"))
            except (json.JSONDecodeError, TypeError):
                d["obstructions"] = []
            result.append(d)
        return result

    # ------------------------------------------------------------------
    # Watch mode
    # ------------------------------------------------------------------

    def set_watch_mode(self, mode: str, eta_minutes: int, expires_at: str, chat_id: str = "") -> None:
        """Activate watch or leaving mode. Deactivates any existing mode first."""
        with self._lock:
            with self._conn:
                self._conn.execute("UPDATE watch_mode SET active = 0 WHERE active = 1")
                self._conn.execute(
                    "INSERT INTO watch_mode (mode, eta_minutes, started_at, expires_at, chat_id, active)"
                    " VALUES (?, ?, ?, ?, ?, 1)",
                    (mode, eta_minutes, datetime.now(timezone.utc).isoformat(), expires_at, chat_id),
                )

    def get_watch_mode(self) -> Optional[dict]:
        """Return the active watch mode as a dict, or None if none active."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM watch_mode WHERE active = 1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
        # Auto-expire if past expires_at
        expires_at = datetime.fromisoformat(result["expires_at"])
        if datetime.now(timezone.utc) >= expires_at:
            self.clear_watch_mode()
            return None
        return result

    def clear_watch_mode(self) -> None:
        """Deactivate all watch modes."""
        with self._lock:
            with self._conn:
                self._conn.execute("UPDATE watch_mode SET active = 0 WHERE active = 1")

    def is_watch_active(self) -> bool:
        """Return True if a watch or leaving mode is currently active and not expired."""
        return self.get_watch_mode() is not None

    # ------------------------------------------------------------------
    # Scan cache
    # ------------------------------------------------------------------

    def save_scan_cache(self, positions: list, summary: str = "") -> None:
        """Save a full scan result to the cache.

        Args:
            positions: List of position result dicts from the street scan.
            summary:   Human-readable summary string (e.g. "Free spaces found: left").
        """
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT INTO scan_cache (positions, summary) VALUES (?, ?)",
                    (json.dumps(positions), summary),
                )
        logger.debug("Saved scan cache with %d positions", len(positions))

    def get_scan_cache(self, max_age_seconds: int = 600) -> Optional[dict]:
        """Return the most recent scan cache entry if younger than *max_age_seconds*.

        Args:
            max_age_seconds: Maximum cache age before it is considered stale.

        Returns:
            Dict with keys ``id``, ``timestamp``, ``positions`` (list),
            ``summary`` (str), and ``age_seconds`` (int), or ``None`` if no
            fresh cache exists.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM scan_cache ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        try:
            ts = datetime.strptime(data["timestamp"], "%Y-%m-%d %H:%M:%S")
            age = (datetime.now() - ts).total_seconds()
            if age > max_age_seconds:
                return None
            data["positions"] = json.loads(data.get("positions", "[]"))
            data["age_seconds"] = int(age)
            return data
        except (ValueError, json.JSONDecodeError):
            return None

    def clear_scan_cache(self) -> None:
        """Delete all scan cache entries."""
        with self._lock:
            with self._conn:
                self._conn.execute("DELETE FROM scan_cache")
        logger.debug("Scan cache cleared")

    # ------------------------------------------------------------------
    # Cost tracking queries
    # ------------------------------------------------------------------

    def get_cost_summary(self) -> dict:
        """
        Return a cost summary dict with today/week/month/all_time totals.

        Returns:
            Dict with keys: today, week, month, all_time (USD floats).
        """
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        day_str = now.strftime("%Y-%m-%d")
        week_cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        month_cutoff = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

        with self._lock:
            today = self._conn.execute(
                "SELECT SUM(estimated_cost) FROM api_costs WHERE date(timestamp) = ?",
                (day_str,),
            ).fetchone()[0] or 0.0
            week = self._conn.execute(
                "SELECT SUM(estimated_cost) FROM api_costs WHERE timestamp >= ?",
                (week_cutoff,),
            ).fetchone()[0] or 0.0
            month = self._conn.execute(
                "SELECT SUM(estimated_cost) FROM api_costs WHERE timestamp >= ?",
                (month_cutoff,),
            ).fetchone()[0] or 0.0
            all_time = self._conn.execute(
                "SELECT SUM(estimated_cost) FROM api_costs"
            ).fetchone()[0] or 0.0

        return {
            "today": round(today, 6),
            "week": round(week, 6),
            "month": round(month, 6),
            "all_time": round(all_time, 6),
        }

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def cleanup_old_records(self, days: int = 90) -> int:
        """
        Delete records older than *days* days from both tables.

        Args:
            days: Retention period in days (default 90).

        Returns:
            Total number of rows deleted.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        deleted = 0

        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    "DELETE FROM checks WHERE timestamp < ?", (cutoff,)
                )
                deleted += cur.rowcount
                cur = self._conn.execute(
                    "DELETE FROM state_changes WHERE timestamp < ?", (cutoff,)
                )
                deleted += cur.rowcount
                cur = self._conn.execute(
                    "DELETE FROM scan_cache WHERE timestamp < ?", (cutoff,)
                )
                deleted += cur.rowcount

        if deleted:
            logger.info("Cleaned up %d old records (older than %d days)", deleted, days)

        return deleted

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            self._conn.close()
        logger.debug("Database connection closed")
