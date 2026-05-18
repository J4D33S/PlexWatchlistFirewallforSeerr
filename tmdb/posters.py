"""
tmdb/posters.py — Fetch poster URLs + metadata from TMDB API.

Cached in SQLite for 30 days. Empty results are NOT cached so
failed fetches are retried on the next run.

Uses a thread pool for concurrent fetching — significantly faster
than sequential fetching when many items need posters.
"""

from __future__ import annotations

import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import NamedTuple

import requests

from config import settings
from utils.logger import get_logger

logger = get_logger("tmdb.posters")

_TMDB_BASE   = "https://api.themoviedb.org/3"
_TMDB_IMG    = "https://image.tmdb.org/t/p/w200"
_TIMEOUT     = 10
_CACHE_TTL   = 60 * 60 * 24 * 30   # 30 days
_MAX_WORKERS = 8                     # concurrent TMDB requests

_DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).parent.parent / "data"))
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_DB_PATH  = _DATA_DIR / "poster_cache.db"


# ── DB helpers ────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS poster_cache (
                tmdb_id    INTEGER NOT NULL,
                media_type TEXT    NOT NULL,
                poster_url TEXT    DEFAULT '',
                is_anime   INTEGER DEFAULT 0,
                fetched_at INTEGER DEFAULT 0,
                PRIMARY KEY (tmdb_id, media_type)
            )
        """)
        # Migration: add is_anime column if missing
        cols = {r[1] for r in conn.execute("PRAGMA table_info(poster_cache)")}
        if "is_anime" not in cols:
            conn.execute("ALTER TABLE poster_cache ADD COLUMN is_anime INTEGER DEFAULT 0")
        conn.commit()


_init_db()


# ── Anime detection ───────────────────────────────────────────────────────────

def _is_anime(data: dict) -> bool:
    """
    Detect anime: Animation genre (id=16) + Japanese origin or language.
    Works with both search result format (genre_ids list) and
    detail endpoint format (genres list of dicts).
    """
    genre_ids = data.get("genre_ids") or [g["id"] for g in data.get("genres", [])]
    if 16 not in genre_ids:
        return False
    origin   = data.get("origin_country") or []
    language = data.get("original_language", "")
    return "JP" in origin or language == "ja"


# ── TMDB fetch ────────────────────────────────────────────────────────────────

class _FetchResult(NamedTuple):
    tmdb_id:    int
    api_type:   str
    orig_type:  str
    poster_url: str
    is_anime:   bool


def _fetch_one(tmdb_id: int, api_type: str, orig_type: str, api_key: str) -> _FetchResult:
    """
    Fetch poster for a single item.
    TV/anime: tries TVDB first, falls back to TMDB.
    Movies: uses TMDB only.
    """
    is_anime = False

    if api_type == "tv":
        # Try TVDB first for TV shows
        try:
            from tvdb.client import get_poster_by_tmdb_id
            tvdb_url = get_poster_by_tmdb_id(tmdb_id)
            if tvdb_url:
                # Still fetch TMDB metadata for anime detection
                tmdb_result = _fetch_tmdb(tmdb_id, "tv", api_key)
                is_anime    = tmdb_result.get("is_anime", False)
                return _FetchResult(tmdb_id, api_type, orig_type, tvdb_url, is_anime)
        except Exception as exc:
            logger.debug("TVDB fetch failed for tmdb_id=%d, falling back to TMDB: %s", tmdb_id, exc)

    # TMDB fallback (or primary for movies)
    result   = _fetch_tmdb(tmdb_id, api_type, api_key)
    is_anime = result.get("is_anime", False) if api_type == "tv" else False
    return _FetchResult(tmdb_id, api_type, orig_type, result.get("poster_url", ""), is_anime)


def _fetch_tmdb(tmdb_id: int, media_type: str, api_key: str) -> dict:
    """Fetch poster and metadata from TMDB."""
    endpoint = "movie" if media_type == "movie" else "tv"
    try:
        resp = requests.get(
            f"{_TMDB_BASE}/{endpoint}/{tmdb_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            params={"language": "en-US"},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 404:
            return {"poster_url": "", "is_anime": False}
        resp.raise_for_status()
        data = resp.json()
        path = data.get("poster_path", "")
        return {
            "poster_url": f"{_TMDB_IMG}{path}" if path else "",
            "is_anime":   _is_anime(data) if media_type == "tv" else False,
        }
    except Exception as exc:
        logger.debug("TMDB fetch failed tmdb_id=%d %s: %s", tmdb_id, media_type, exc)
        return {"poster_url": "", "is_anime": False}


# Backward compat alias
def _fetch_from_tmdb(tmdb_id: int, media_type: str) -> dict:
    return _fetch_tmdb(tmdb_id, media_type, settings.tmdb_api_key)


# ── Public API ────────────────────────────────────────────────────────────────

def get_poster_urls_bulk(
    items: list[dict],
    bypass_cache: bool = False,
) -> dict[tuple, str]:
    """
    Fetch poster URLs for a list of dicts with 'tmdb_id' and 'type'.
    Returns dict of (tmdb_id, original_type) -> poster_url.

    Uses a thread pool for concurrent fetching.
    bypass_cache=True forces re-fetch from TMDB (used by backfill).
    """
    api_key = settings.tmdb_api_key
    if not api_key:
        return {}

    now    = int(time.time())
    result: dict[tuple, str] = {}
    needed: list[tuple]      = []   # (tmdb_id, api_type, orig_type)

    if not bypass_cache:
        with _get_conn() as conn:
            for item in items:
                tid      = item["tmdb_id"]
                orig     = item["type"]
                api_type = "tv" if orig in ("tv", "anime") else "movie"
                row = conn.execute(
                    "SELECT poster_url, fetched_at FROM poster_cache "
                    "WHERE tmdb_id=? AND media_type=?",
                    (tid, api_type),
                ).fetchone()
                if row and row["poster_url"] and (now - row["fetched_at"]) < _CACHE_TTL:
                    result[(tid, orig)] = row["poster_url"]
                else:
                    needed.append((tid, api_type, orig))
    else:
        needed = [
            (item["tmdb_id"],
             "tv" if item["type"] in ("tv", "anime") else "movie",
             item["type"])
            for item in items
        ]

    if not needed:
        return result

    logger.info("Posters: %d cached, %d fetching from TMDB (concurrent)", len(result), len(needed))

    # Fetch concurrently
    fetched_results: list[_FetchResult] = []
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_one, tid, api_type, orig, api_key): (tid, orig)
            for tid, api_type, orig in needed
        }
        for future in as_completed(futures):
            try:
                fetched_results.append(future.result())
            except Exception as exc:
                tid, orig = futures[future]
                logger.warning("Poster fetch error tmdb_id=%d: %s", tid, exc)

    # Write results to cache and build return map
    with _get_conn() as conn:
        for r in fetched_results:
            result[(r.tmdb_id, r.orig_type)] = r.poster_url
            if r.poster_url:   # only cache successes
                conn.execute(
                    "INSERT OR REPLACE INTO poster_cache "
                    "(tmdb_id, media_type, poster_url, is_anime, fetched_at) "
                    "VALUES (?,?,?,?,?)",
                    (r.tmdb_id, r.api_type, r.poster_url, 1 if r.is_anime else 0, now),
                )
        conn.commit()

    return result


def get_anime_tmdb_ids() -> set[int]:
    """Return TMDB IDs flagged as anime in the poster cache."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT tmdb_id FROM poster_cache WHERE is_anime=1"
        ).fetchall()
    return {r["tmdb_id"] for r in rows}


def clear_cache() -> int:
    """Delete all cached poster entries. Returns count deleted."""
    with _get_conn() as conn:
        cur = conn.execute("DELETE FROM poster_cache")
        conn.commit()
    return cur.rowcount
