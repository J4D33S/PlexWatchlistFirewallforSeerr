"""
engine/processor.py — Decision pipeline.

Orchestrates all data fetching and runs each watchlist item
through the rules engine to produce a Decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from config import settings
from engine.rules import RulesEngine
from seerr.client import SeerrClient
from plex.watchlist import WatchlistItem, get_all_watchlists
from plex.library import get_plex_tmdb_ids
from storage.ignore_list import IgnoreList, ignore_list as default_ignore_list
from utils.logger import get_logger, log_decision

logger = get_logger("engine.processor")


@dataclass
class Decision:
    status: str
    reason: str
    item:   dict[str, Any]
    action: str = ""
    seerr_response: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status":         self.status,
            "reason":         self.reason,
            "item":           self.item,
            "action":         self.action,
            "seerr_response": self.seerr_response,
        }


class Processor:
    def __init__(
        self,
        seerr_client: SeerrClient | None = None,
        ignore:       IgnoreList  | None = None,
        rules_engine: RulesEngine | None = None,
        dry_run:      bool        | None = None,
    ) -> None:
        self.client  = seerr_client or SeerrClient()
        self.ignore  = ignore       or default_ignore_list
        self.engine  = rules_engine or RulesEngine()
        self.dry_run = dry_run if dry_run is not None else settings.dry_run

    # ── Seerr blacklist sync ─────────────────────────────────────────────────

    def _sync_seerr_blacklist(self) -> None:
        """
        Pull Seerr's blacklist and add new entries to the firewall block list.
        Only adds entries not already present — never removes existing ones.
        Fetches poster from TMDB for each new entry.
        """
        from tmdb.posters import get_poster_urls_bulk
        try:
            blacklist = self.client.get_blacklist()
        except Exception as exc:
            logger.warning("Could not fetch Seerr blacklist: %s", exc)
            return

        new_items = [
            {"tmdb_id": int(item["tmdbId"]), "title": item.get("title", ""), "type": item.get("mediaType", "movie")}
            for item in blacklist
            if item.get("tmdbId") and not self.ignore.is_blocked(int(item["tmdbId"]))[0]
        ]

        if not new_items:
            logger.debug("Seerr blacklist sync: no new items")
            return

        poster_map = get_poster_urls_bulk(new_items)
        for item in new_items:
            self.ignore.add_tmdb(
                tmdb_id=item["tmdb_id"],
                title=item["title"],
                reason="Synced from Seerr blacklist",
                poster_url=poster_map.get((item["tmdb_id"], item["type"]), ""),
            )
        logger.info("Synced %d new item(s) from Seerr blacklist", len(new_items))

    # ── Main pipeline ────────────────────────────────────────────────────────

    def process_all_users(self) -> list[Decision]:
        """
        Full pipeline:
        1. Sync Seerr blacklist into firewall block list
        2. Fetch all user watchlists
        3. Fetch Seerr requests, availability, and Plex library in parallel context
        4. Run each item through rules engine
        """
        self._sync_seerr_blacklist()

        logger.info("Fetching watchlists from all Seerr users …")
        items = get_all_watchlists(self.client)
        if not items:
            logger.warning("No watchlist items found across all users.")
            return []

        logger.info("Fetching Seerr requests …")
        request_status_map = self.client.get_request_status_map()
        existing_tmdb_ids  = set(request_status_map.keys())
        pending_count      = sum(1 for v in request_status_map.values() if v["status"] == 1)
        logger.info("Seerr: %d active requests (%d pending)", len(existing_tmdb_ids), pending_count)

        logger.info("Fetching Seerr available/partial media …")
        media_status_map    = self.client.get_media_status_map()
        seerr_available_ids = set(media_status_map.keys())

        logger.info("Fetching Plex library …")
        plex_library_ids = get_plex_tmdb_ids()
        logger.info(
            "Plex library: %d items" if plex_library_ids else "Plex not configured — skipping",
            len(plex_library_ids),
        )

        seen:      set[int]     = set()
        decisions: list[Decision] = []
        for item in items:
            decisions.append(self._process_one(
                item,
                existing_tmdb_ids=existing_tmdb_ids,
                request_status_map=request_status_map,
                media_status_map=media_status_map,
                seen_in_session=seen,
                seerr_available_ids=seerr_available_ids,
                plex_library_ids=plex_library_ids,
            ))
            seen.add(item.tmdb_id)

        return decisions

    def _process_one(
        self,
        item: WatchlistItem,
        existing_tmdb_ids:  set[int],
        request_status_map: dict,
        media_status_map:   dict[int, str],
        seen_in_session:    set[int],
        seerr_available_ids: set[int],
        plex_library_ids:   set[int],
    ) -> Decision:
        status, reason = self.engine.evaluate(
            item,
            ignore=self.ignore,
            existing_tmdb_ids=existing_tmdb_ids,
            seen_in_session=seen_in_session,
            seerr_available_ids=seerr_available_ids,
            plex_library_ids=plex_library_ids,
        )

        req_info     = request_status_map.get(item.tmdb_id, {})
        # Get media status — from request map first, fall back to media status map
        media_status = (
            req_info.get("media_status_label")
            or media_status_map.get(item.tmdb_id, "")
        )

        if status == "ALLOW":
            if self.dry_run:
                action = "DRY RUN — request NOT sent to Seerr"
            else:
                self.client.create_request(tmdb_id=item.tmdb_id, media_type=item.type)
                action = "request forwarded to Seerr"
        elif status == "BLOCK":
            action = "dropped — not forwarded"
        else:
            action = reason  # SKIP

        log_decision(logger, status=status, title=str(item), reason=reason, dry_run=self.dry_run)  # type: ignore[arg-type]

        return Decision(
            status=status,
            reason=reason,
            item={
                "tmdb_id":      item.tmdb_id,
                "title":        item.title,
                "type":         item.type,
                "added_by":     item.added_by,
                "added_at":     item.added_at,
                "req_status":   req_info.get("status_label", ""),
                "media_status": media_status,
                "seerr_req_at": req_info.get("created_at", ""),
                "poster_url":   "",
            },
        )
