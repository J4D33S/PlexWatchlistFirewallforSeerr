"""
storage/ignore_list.py — SQLite-backed block list.

Supports blocking by:
  - TMDB ID  (exact match)
  - TVDB ID  (looked up → TMDB ID stored)
  - Keyword  (case-insensitive title substring)

Handles schema migration automatically.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from utils.logger import get_logger

logger = get_logger("storage.ignore_list")

_DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).parent.parent / "data"))
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_DB_PATH = _DATA_DIR / "ignore_list.db"

_REQUIRED_COLS = {"id", "tmdb_id", "title", "keyword", "reason", "added_at", "poster_url", "tvdb_id", "media_type"}


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _get_columns() -> set[str]:
    try:
        with _get_conn() as conn:
            return {row[1] for row in conn.execute("PRAGMA table_info(ignore_list)")}
    except Exception:
        return set()


def _init_db() -> None:
    existing = _get_columns()

    if existing and not _REQUIRED_COLS.issubset(existing):
        missing = _REQUIRED_COLS - existing
        # Soft migration — add missing columns without losing data
        soft_addable = {"poster_url", "tvdb_id", "media_type"}
        if missing.issubset(soft_addable):
            with _get_conn() as conn:
                migrations = {
                    "poster_url": "ALTER TABLE ignore_list ADD COLUMN poster_url TEXT DEFAULT ''",
                    "tvdb_id":    "ALTER TABLE ignore_list ADD COLUMN tvdb_id INTEGER DEFAULT 0",
                    "media_type": "ALTER TABLE ignore_list ADD COLUMN media_type TEXT DEFAULT ''",
                }
                for col in missing:
                    conn.execute(migrations[col])
                    logger.info("DB migration: added column '%s'", col)
                conn.commit()
            _create_table()
            return

        # Hard migration — rebuild table preserving what we can
        logger.warning("Block list DB schema outdated — rebuilding.")
        with _get_conn() as conn:
            saved: list[tuple] = []
            try:
                saved = conn.execute(
                    "SELECT tmdb_id, title, reason FROM ignore_list WHERE tmdb_id IS NOT NULL"
                ).fetchall()
            except Exception:
                pass
            conn.execute("DROP TABLE IF EXISTS ignore_list")
            conn.commit()
        _create_table()
        if saved:
            with _get_conn() as conn:
                for row in saved:
                    conn.execute(
                        "INSERT OR IGNORE INTO ignore_list (tmdb_id, title, reason) VALUES (?,?,?)",
                        (row[0], row[1] or "", row[2] or ""),
                    )
                conn.commit()
        logger.info("Recovered %d entries from old schema", len(saved))
    else:
        _create_table()


def _create_table() -> None:
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ignore_list (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                tmdb_id    INTEGER,
                tvdb_id    INTEGER DEFAULT 0,
                media_type TEXT    DEFAULT '',
                title      TEXT    DEFAULT '',
                keyword    TEXT    DEFAULT '',
                reason     TEXT    DEFAULT '',
                poster_url TEXT    DEFAULT '',
                added_at   TEXT    DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_tmdb_id ON ignore_list(tmdb_id) WHERE tmdb_id IS NOT NULL")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_keyword  ON ignore_list(keyword)  WHERE keyword  IS NOT NULL AND keyword != ''")
        conn.commit()


_init_db()


class IgnoreList:
    """SQLite-backed block list supporting TMDB ID, TVDB ID, and keyword entries."""

    def add_tmdb(
        self,
        tmdb_id:    int,
        title:      str = "",
        reason:     str = "",
        poster_url: str = "",
        tvdb_id:    int = 0,
        media_type: str = "",
    ) -> None:
        with _get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ignore_list "
                "(tmdb_id, tvdb_id, media_type, title, reason, poster_url) VALUES (?,?,?,?,?,?)",
                (tmdb_id, tvdb_id, media_type, title.strip(), reason.strip(), poster_url),
            )
            conn.commit()
        logger.info("BlockList ADD tmdb_id=%d title=%r", tmdb_id, title)

    def add_keyword(self, keyword: str, reason: str = "") -> None:
        kw = keyword.strip().lower()
        if not kw:
            return
        with _get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ignore_list (keyword, reason) VALUES (?,?)",
                (kw, reason.strip()),
            )
            conn.commit()
        logger.info("BlockList ADD keyword=%r", kw)

    def remove_by_id(self, row_id: int) -> bool:
        with _get_conn() as conn:
            cur = conn.execute("DELETE FROM ignore_list WHERE id=?", (row_id,))
            conn.commit()
        return cur.rowcount > 0

    def is_blocked(self, tmdb_id: int, title: str = "") -> tuple[bool, str]:
        with _get_conn() as conn:
            if conn.execute("SELECT 1 FROM ignore_list WHERE tmdb_id=?", (tmdb_id,)).fetchone():
                return True, f"tmdb_id={tmdb_id} is on the block list"
            if title:
                for row in conn.execute("SELECT keyword FROM ignore_list WHERE keyword IS NOT NULL AND keyword!=''").fetchall():
                    if row["keyword"] in title.lower():
                        return True, f"title matches blocked keyword '{row['keyword']}'"
        return False, ""

    def all_entries(self, search: str = "") -> list[dict]:
        with _get_conn() as conn:
            if search:
                pat = f"%{search.lower()}%"
                rows = conn.execute("""
                    SELECT id, tmdb_id, tvdb_id, media_type, title, keyword, reason, poster_url, added_at
                    FROM ignore_list
                    WHERE lower(coalesce(title,''))   LIKE ?
                       OR lower(coalesce(keyword,'')) LIKE ?
                       OR lower(coalesce(reason,''))  LIKE ?
                    ORDER BY added_at DESC
                """, (pat, pat, pat)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT id, tmdb_id, tvdb_id, media_type, title, keyword, reason, poster_url, added_at
                    FROM ignore_list ORDER BY added_at DESC
                """).fetchall()
        return [dict(r) for r in rows]

    def all_ids(self) -> frozenset[int]:
        with _get_conn() as conn:
            return frozenset(
                r["tmdb_id"] for r in
                conn.execute("SELECT tmdb_id FROM ignore_list WHERE tmdb_id IS NOT NULL").fetchall()
            )

    def count(self) -> int:
        try:
            with _get_conn() as conn:
                return conn.execute("SELECT COUNT(*) FROM ignore_list").fetchone()[0]
        except Exception:
            return 0

    def __len__(self) -> int:
        return self.count()


ignore_list = IgnoreList()
