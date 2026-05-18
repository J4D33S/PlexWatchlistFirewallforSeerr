"""
plex/watchlist.py — Fetch watchlists for all Seerr users.

Media types:
  movie  — films
  tv     — TV shows
  anime  — detected via TMDB genre (Animation genre_id=16 + origin_country=JP)
             or via Plex AnimeTvShows section membership
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from utils.logger import get_logger

logger = get_logger("plex.watchlist")

MediaType = Literal["movie", "tv", "anime"]


@dataclass
class WatchlistItem:
    """Normalised Plex watchlist entry."""

    tmdb_id: int
    title: str
    type: MediaType
    added_by: str = "unknown"
    seerr_user_id: int = 0
    added_at: str = ""
    extra: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.title} ({self.type}, tmdb:{self.tmdb_id}, user:{self.added_by})"


def _media_type(raw: str) -> MediaType:
    if raw.lower() in ("tv", "show", "series"):
        return "tv"
    return "movie"


def get_all_watchlists(client) -> list[WatchlistItem]:
    users = client.get_users()
    if not users:
        logger.warning("No Seerr users returned — check your API key and URL.")
        return []

    all_items: list[WatchlistItem] = []

    for user in users:
        user_id      = user.get("id", 0)
        display_name = (
            user.get("displayName")
            or user.get("username")
            or user.get("email", "unknown")
        )

        logger.info("Fetching watchlist for user: %s (id=%d)", display_name, user_id)
        raw_items = client.get_user_watchlist(user_id)

        if not raw_items:
            logger.info("  → No watchlist items for %s", display_name)
            continue

        for item in raw_items:
            tmdb_id = item.get("tmdbId") or item.get("id")
            if not tmdb_id:
                continue

            title      = item.get("title") or item.get("name") or "Unknown"
            media_type = _media_type(item.get("mediaType") or item.get("type") or "movie")
            added_at   = str(item.get("addedAt") or "")

            all_items.append(WatchlistItem(
                tmdb_id=int(tmdb_id),
                title=title,
                type=media_type,
                added_by=display_name,
                seerr_user_id=user_id,
                added_at=added_at,
                extra=item,
            ))

        logger.info("  → %d items from %s", len(raw_items), display_name)

    logger.info("Total watchlist items across all users: %d", len(all_items))
    return all_items
