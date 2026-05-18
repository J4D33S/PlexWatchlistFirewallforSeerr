"""
seerr/client.py — Seerr REST API wrapper.

All GET calls are safe/read-only.
POST calls are gated by dry_run — no writes when dry_run=True.
create_request() accepts force=True to bypass dry_run for manual forwards.
"""

from __future__ import annotations

import json
from typing import Any

import requests

from config import settings
from utils.logger import get_logger

logger = get_logger("seerr.client")

_TIMEOUT = 15

_REQ_STATUS   = {1: "pending", 2: "approved", 3: "declined", 4: "partial",  5: "available"}
_MEDIA_STATUS = {1: "unknown", 2: "pending",  3: "processing", 4: "partial", 5: "available"}


class SeerrClient:
    """Client for the Seerr REST API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key:  str | None = None,
        dry_run:  bool | None = None,
    ) -> None:
        self.base_url = (base_url or settings.seerr_url).rstrip("/")
        self.api_key  = api_key  or settings.seerr_api_key
        self.dry_run  = dry_run if dry_run is not None else settings.dry_run

        self._session = requests.Session()
        self._session.headers.update({
            "X-Api-Key":    self.api_key,
            "Content-Type": "application/json",
            "Accept":       "application/json",
        })

    def _url(self, path: str) -> str:
        return f"{self.base_url}/api/v1{path}"

    def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        try:
            resp = self._session.get(self._url(path), params=params, timeout=_TIMEOUT)
            if not resp.ok:
                logger.error(
                    "Seerr GET error: %s %s — %s",
                    resp.status_code, resp.reason, self._url(path),
                )
                return {}
            return resp.json()
        except requests.exceptions.ConnectionError:
            logger.warning("Cannot reach Seerr at %s", self.base_url)
        except Exception as exc:
            logger.error("Seerr GET exception: %s", exc)
        return {}

    def _post(self, path: str, payload: dict, force: bool = False) -> dict[str, Any]:
        """POST to Seerr. force=True bypasses dry_run for manual forwards."""
        if self.dry_run and not force:
            logger.info("DRY RUN — would POST %s: %s", self._url(path), json.dumps(payload))
            return {"dry_run": True, "simulated": True, "payload": payload}
        try:
            resp = self._session.post(self._url(path), json=payload, timeout=_TIMEOUT)
            if not resp.ok:
                logger.error(
                    "Seerr POST error: %s %s\n  Payload: %s\n  Response: %s",
                    resp.status_code, resp.reason,
                    json.dumps(payload),
                    resp.text[:500],
                )
                return {"error": f"{resp.status_code} {resp.reason}", "detail": resp.text[:500]}
            return resp.json()
        except requests.exceptions.ConnectionError:
            logger.error("Cannot reach Seerr at %s", self.base_url)
            return {"error": "connection_failed"}
        except Exception as exc:
            logger.error("Seerr POST exception: %s", exc)
            return {"error": str(exc)}

    def _paginate(self, path: str, params: dict | None = None) -> list[dict]:
        """Fetch all pages from a paginated endpoint."""
        results: list[dict] = []
        skip, take = 0, 100
        base = {**(params or {}), "take": take}
        while True:
            data = self._get(path, params={**base, "skip": skip})
            if not data:
                break
            page = data.get("results", [])
            results.extend(page)
            skip += take
            if skip >= data.get("pageInfo", {}).get("results", 0) or not page:
                break
        return results

    # ── Users ────────────────────────────────────────────────────────────────

    def get_users(self) -> list[dict[str, Any]]:
        users: list[dict] = []
        skip, take = 0, 50
        while True:
            data = self._get("/user", params={"take": take, "skip": skip, "sort": "created"})
            if not data:
                break
            page = data.get("results", [])
            users.extend(page)
            skip += take
            if skip >= data.get("pageInfo", {}).get("results", 0) or not page:
                break
        logger.info("Fetched %d Seerr users", len(users))
        return users

    def get_user_watchlist(self, user_id: int) -> list[dict[str, Any]]:
        items: list[dict] = []
        page = 1
        while True:
            data = self._get(f"/user/{user_id}/watchlist", params={"page": page})
            if not data:
                break
            results = data.get("results", [])
            items.extend(results)
            if page >= data.get("totalPages", 1) or not results:
                break
            page += 1
        return items

    # ── Requests ─────────────────────────────────────────────────────────────

    def get_request_status_map(self) -> dict[int, dict]:
        """Return map of tmdb_id -> request info including status labels."""
        status_map: dict[int, dict] = {}
        for req in self._paginate("/request", {"filter": "all"}):
            media   = req.get("media", {})
            tmdb_id = media.get("tmdbId")
            if not tmdb_id:
                continue
            status_map[int(tmdb_id)] = {
                "request_id":        req.get("id"),
                "status":            req.get("status", 0),
                "status_label":      _REQ_STATUS.get(req.get("status", 0), ""),
                "media_status":      media.get("status", 0),
                "media_status_label": _MEDIA_STATUS.get(media.get("status", 0), ""),
                "created_at":        req.get("createdAt", ""),
                "requested_by":      req.get("requestedBy", {}).get("displayName", ""),
            }
        return status_map

    def get_existing_tmdb_ids(self) -> set[int]:
        return set(self.get_request_status_map().keys())

    def get_available_tmdb_ids(self) -> set[int]:
        """Return TMDB IDs Seerr marks as available or partially available."""
        return set(self.get_media_status_map().keys())

    def get_media_status_map(self) -> dict[int, str]:
        """
        Return map of tmdb_id -> media status label for available/partial items.
        Labels: 'available' or 'partial'
        """
        status_map: dict[int, str] = {}
        for filter_val, label in (("available", "available"), ("partial", "partial")):
            for item in self._paginate("/media", {"filter": filter_val}):
                tmdb_id = item.get("tmdbId")
                if tmdb_id:
                    status_map[int(tmdb_id)] = label
        logger.info("Seerr reports %d available/partial items", len(status_map))
        return status_map

    def get_blacklist(self) -> list[dict[str, Any]]:
        """Return Seerr's blacklisted items."""
        items = self._paginate("/blacklist")
        logger.info("Seerr blacklist has %d items", len(items))
        return items

    def create_request(
        self,
        tmdb_id: int,
        media_type: str,
        seasons: list[int] | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """
        POST /api/v1/request.
        force=True bypasses dry_run — used for manual per-item forwards.
        """
        payload: dict[str, Any] = {
            "mediaType": "tv" if media_type in ("tv", "anime") else "movie",
            "mediaId":   tmdb_id,
        }
        # TV requests require a seasons field — default to all seasons
        if media_type in ("tv", "anime"):
            payload["seasons"] = seasons if seasons else "all"
        return self._post("/request", payload, force=force)
