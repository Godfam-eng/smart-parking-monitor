"""
state.py — SQLite database wrapper for parking history.

Records every parking check and tracks state changes.
Provides aggregated statistics for the /stats command.
"""

import sqlite3
import logging
from datetime import datetime

import config

logger = logging.getLogger(__name__)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they do not already exist."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS checks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                checked_at  TEXT    NOT NULL,
                status      TEXT    NOT NULL,  -- FREE or OCCUPIED
                confidence  TEXT    NOT NULL,
                description TEXT,
                position    TEXT,              -- scan position label or 'home'
                notified    INTEGER DEFAULT 0  -- 1 if a notification was sent
            )
            """
        )
        conn.commit()
    logger.debug("Database initialised at %s", config.DB_PATH)


def record_check(
    status: str,
    confidence: str,
    description: str,
    position: str = "home",
    notified: bool = False,
) -> int:
    """Insert a new check record. Returns the new row id."""
    now = datetime.utcnow().isoformat(timespec="seconds")
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO checks (checked_at, status, confidence, description, position, notified)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (now, status.upper(), confidence.lower(), description, position, int(notified)),
        )
        conn.commit()
    return cur.lastrowid


def get_last_check(position: str = "home") -> sqlite3.Row | None:
    """Return the most recent check for *position*, or None."""
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM checks WHERE position = ? ORDER BY id DESC LIMIT 1",
            (position,),
        ).fetchone()


def has_state_changed(new_status: str, position: str = "home") -> bool:
    """Return True if *new_status* differs from the most recent recorded status."""
    last = get_last_check(position)
    if last is None:
        return True
    return last["status"] != new_status.upper()


def get_stats() -> dict:
    """Return aggregated statistics for the /stats command."""
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM checks").fetchone()[0]
        free_count = conn.execute(
            "SELECT COUNT(*) FROM checks WHERE status = 'FREE'"
        ).fetchone()[0]
        by_hour = conn.execute(
            """
            SELECT
                CAST(strftime('%H', checked_at) AS INTEGER) AS hour,
                COUNT(*) AS total,
                SUM(CASE WHEN status = 'FREE' THEN 1 ELSE 0 END) AS free
            FROM checks
            GROUP BY hour
            ORDER BY hour
            """
        ).fetchall()

    free_pct = round(100 * free_count / total, 1) if total else 0
    busiest = None
    lowest_free_pct = 101
    for row in by_hour:
        if row["total"] > 0:
            pct = 100 * row["free"] / row["total"]
            if pct < lowest_free_pct:
                lowest_free_pct = pct
                busiest = row["hour"]

    return {
        "total_checks": total,
        "free_count": free_count,
        "free_percentage": free_pct,
        "by_hour": [dict(r) for r in by_hour],
        "busiest_hour": busiest,
    }
