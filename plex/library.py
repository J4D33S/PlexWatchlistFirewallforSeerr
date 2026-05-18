"""
plex/library.py — Check if media items exist in the Plex library.

Uses the Plex HTTP API to fetch all GUIDs (TMDB IDs) across
movie and TV sections. Returns a set of TMDB IDs that are
confirmed present in Plex — i.e. actually transferred and playable.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import urllib3
import requests

from config import settings

# Plex servers commonly use self-signed certs — suppress the resulting warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from utils.logger import get_logger

logger = get_logger("plex.library")

_TIMEOUT = 15


def _get_plex_sections(plex_url: str, token: str) -> list[dict]:
    """Fetch all library sections from Plex."""
    try:
        resp = requests.get(
            f"{plex_url}/library/sections",
            headers={"X-Plex-Token": token, "Accept": "application/xml"},
            timeout=_TIMEOUT,
            verify=False,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        sections = []
        for directory in root.findall("Directory"):
            sections.append({
                "key":   directory.get("key"),
                "type":  directory.get("type"),   # movie or show
                "title": directory.get("title"),
            })
        return sections
    except Exception as exc:
        logger.warning("Could not fetch Plex sections: %s", exc)
        return []


def _get_section_tmdb_ids(plex_url: str, token: str, section_key: str) -> set[int]:
    """Fetch all TMDB IDs from a single Plex library section."""
    tmdb_ids: set[int] = set()
    try:
        resp = requests.get(
            f"{plex_url}/library/sections/{section_key}/all",
            headers={"X-Plex-Token": token, "Accept": "application/xml"},
            params={"includeGuids": 1},
            timeout=_TIMEOUT,
            verify=False,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)

        # Both Video (movies) and Directory (shows) elements carry Guid children
        for item in root.findall("Video") + root.findall("Directory"):
            for guid in item.findall("Guid"):
                guid_id = guid.get("id", "")
                # Format: tmdb://12345
                if guid_id.startswith("tmdb://"):
                    try:
                        tmdb_ids.add(int(guid_id.replace("tmdb://", "")))
                    except ValueError:
                        pass
    except Exception as exc:
        logger.warning("Could not fetch section %s from Plex: %s", section_key, exc)
    return tmdb_ids


def get_plex_tmdb_ids() -> set[int]:
    """
    Return the set of TMDB IDs for all media confirmed present in Plex.
    Returns empty set if Plex URL/token not configured or unreachable.
    """
    plex_url = settings.plex_url
    token    = settings.plex_token

    if not plex_url or not token:
        logger.debug("Plex URL or token not configured — skipping Plex library check.")
        return set()

    logger.info("Fetching Plex library TMDB IDs from %s …", plex_url)
    sections = _get_plex_sections(plex_url, token)

    if not sections:
        logger.warning("No Plex library sections found — check your Plex URL and token.")
        return set()

    all_ids: set[int] = set()
    for section in sections:
        if section["type"] in ("movie", "show"):
            ids = _get_section_tmdb_ids(plex_url, token, section["key"])
            logger.info(
                "  Plex section '%s' (%s): %d items",
                section["title"], section["type"], len(ids)
            )
            all_ids.update(ids)

    logger.info("Total TMDB IDs in Plex library: %d", len(all_ids))
    return all_ids
