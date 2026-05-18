"""
tvdb/client.py — TVDB v4 API client.

Authenticates using API key + PIN to get a bearer token.
Token is cached in SQLite and refreshed every 23 hours.
Used for TV show and anime poster images.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import requests

from config import settings
from utils.logger import get_logger

logger = get_logger("tvdb.client")

_BASE      = "https://api4.thetvdb.com/v4"
_IMG       = "https://artworks.thetvdb.com"
_TIMEOUT   = 10
_TOKEN_TTL = 60 * 60 * 23   # 23 hours
_CACHE_TTL = 60 * 60 * 24 * 30  # 30 days

_DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).parent.parent / "data"))
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_DB_PATH  = _DATA_DIR / "tvdb_cache.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tvdb_token (
                id         INTEGER PRIMARY KEY CHECK (id = 1),
                token      TEXT    DEFAULT '',
                fetched_at INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tvdb_poster_cache (
                tmdb_id    INTEGER PRIMARY KEY,
                poster_url TEXT    DEFAULT '',
                fetched_at INTEGER DEFAULT 0
            )
        """)
        conn.commit()


_init_db()


def _get_token() -> str:
    """Return a valid TVDB bearer token, refreshing from API if expired."""
    if not settings.tvdb_api_key or not settings.tvdb_pin:
        return ""

    now = int(time.time())
    with _get_conn() as conn:
        row = conn.execute("SELECT token, fetched_at FROM tvdb_token WHERE id=1").fetchone()
    if row and row["token"] and (now - row["fetched_at"]) < _TOKEN_TTL:
        return row["token"]

    try:
        resp = requests.post(
            f"{_BASE}/login",
            json={"apikey": settings.tvdb_api_key, "pin": settings.tvdb_pin},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        token = resp.json().get("data", {}).get("token", "")
        if token:
            with _get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO tvdb_token (id, token, fetched_at) VALUES (1,?,?)",
                    (token, now),
                )
                conn.commit()
            logger.info("TVDB token refreshed successfully")
        return token
    except Exception as exc:
        logger.warning("TVDB authentication failed: %s", exc)
        return ""


def _get_series_poster(tvdb_series_id: str | int, headers: dict) -> str:
    """Fetch the primary poster for a TVDB series ID."""
    try:
        resp = requests.get(
            f"{_BASE}/series/{tvdb_series_id}/artworks",
            headers=headers,
            params={"type": 2},   # 2 = poster
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            artworks = resp.json().get("data", {}).get("artworks", []) or []
            if artworks:
                path = artworks[0].get("image", "")
                if path:
                    return path if path.startswith("http") else f"{_IMG}{path}"
    except Exception as exc:
        logger.debug("TVDB artwork fetch failed series=%s: %s", tvdb_series_id, exc)
    return ""


def get_poster_by_tmdb_id(tmdb_id: int) -> str:
    """
    Look up a TV show poster on TVDB using the TMDB ID.
    Uses TVDB's remote ID search to cross-reference.
    Returns poster URL or empty string. Results cached 30 days.
    """
    token = _get_token()
    if not token:
        return ""

    now = int(time.time())

    # Check cache first
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT poster_url, fetched_at FROM tvdb_poster_cache WHERE tmdb_id=?",
            (tmdb_id,),
        ).fetchone()
    if row and (now - row["fetched_at"]) < _CACHE_TTL:
        return row["poster_url"]  # may be empty string (cached miss)

    headers  = {"Authorization": f"Bearer {token}"}
    poster   = ""

    try:
        # Search TVDB by TMDB remote ID
        resp = requests.get(
            f"{_BASE}/search",
            headers=headers,
            params={"remoteId": f"tmdb-{tmdb_id}"},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            results = resp.json().get("data") or []
            if results:
                item     = results[0]
                # Direct image on search result
                image    = item.get("image_url") or item.get("thumbnail") or ""
                tvdb_sid = item.get("tvdb_id") or item.get("id") or ""

                if image:
                    poster = image if image.startswith("http") else f"{_IMG}{image}"
                elif tvdb_sid:
                    poster = _get_series_poster(tvdb_sid, headers)

    except Exception as exc:
        logger.debug("TVDB lookup failed tmdb_id=%d: %s", tmdb_id, exc)

    # Cache result (including empty string misses — avoids hammering API)
    with _get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO tvdb_poster_cache (tmdb_id, poster_url, fetched_at) VALUES (?,?,?)",
            (tmdb_id, poster, now),
        )
        conn.commit()

    return poster


def clear_cache() -> int:
    """Clear TVDB poster cache. Returns count deleted."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM tvdb_poster_cache")
        conn.commit()
    return cur.rowcount
