"""
storage/run_cache.py — Persist the last firewall run to SQLite.
Survives container restarts and updates.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from utils.logger import get_logger

logger = get_logger("storage.run_cache")

_DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).parent.parent / "data"))
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_DB_PATH  = _DATA_DIR / "run_cache.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS last_run (
                id          INTEGER PRIMARY KEY CHECK (id = 1),
                ran_at      TEXT DEFAULT '',
                decisions   TEXT DEFAULT '[]',
                summary     TEXT DEFAULT '{}',
                dry_run     INTEGER DEFAULT 1,
                users       TEXT DEFAULT '[]',
                error       TEXT DEFAULT NULL
            )
        """)
        conn.commit()


_init_db()

_EMPTY_RUN = {
    "ran_at":    None,
    "decisions": [],
    "summary":   {"ALLOW": 0, "BLOCK": 0, "SKIP": 0, "TOTAL": 0},
    "dry_run":   True,
    "users":     [],
    "error":     None,
}


def load_last_run() -> dict:
    """Load the last run from SQLite. Returns empty run dict if none exists."""
    try:
        with _get_conn() as conn:
            row = conn.execute("SELECT * FROM last_run WHERE id=1").fetchone()
        if not row:
            return dict(_EMPTY_RUN)
        return {
            "ran_at":    row["ran_at"] or None,
            "decisions": json.loads(row["decisions"] or "[]"),
            "summary":   json.loads(row["summary"]   or "{}"),
            "dry_run":   bool(row["dry_run"]),
            "users":     json.loads(row["users"]     or "[]"),
            "error":     row["error"],
        }
    except Exception as exc:
        logger.warning("Could not load run cache: %s", exc)
        return dict(_EMPTY_RUN)


def save_last_run(run: dict) -> None:
    """Persist the last run to SQLite."""
    try:
        with _get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO last_run
                    (id, ran_at, decisions, summary, dry_run, users, error)
                VALUES (1, ?, ?, ?, ?, ?, ?)
            """, (
                run.get("ran_at", ""),
                json.dumps(run.get("decisions", [])),
                json.dumps(run.get("summary",   {})),
                1 if run.get("dry_run") else 0,
                json.dumps(run.get("users",     [])),
                run.get("error"),
            ))
            conn.commit()
        logger.debug("Run cache saved (%d decisions)", len(run.get("decisions", [])))
    except Exception as exc:
        logger.warning("Could not save run cache: %s", exc)
