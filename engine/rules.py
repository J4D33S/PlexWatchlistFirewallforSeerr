"""
engine/rules.py — Rule evaluation for each watchlist item.

Purpose: replacement for Seerr's native Plex watchlist sync.
Prevents duplicate/repeat requests for ongoing or partial shows.

Rules run in priority order; first failure short-circuits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from plex.watchlist import WatchlistItem
from storage.ignore_list import IgnoreList
from utils.logger import get_logger

logger = get_logger("engine.rules")


@dataclass(frozen=True)
class RuleResult:
    passed: bool
    reason: str


def rule_block_list(item: WatchlistItem, ignore: IgnoreList, **_) -> RuleResult:
    """Block items explicitly on the block list (by TMDB ID or keyword)."""
    blocked, reason = ignore.is_blocked(tmdb_id=item.tmdb_id, title=item.title)
    if blocked:
        return RuleResult(False, reason)
    return RuleResult(True, "not on block list")


def rule_already_requested_in_seerr(
    item: WatchlistItem,
    existing_tmdb_ids: set[int],
    **_,
) -> RuleResult:
    """
    Skip if Seerr already has any active request for this item.
    Covers: pending, approved, processing — it's already being handled.
    """
    if item.tmdb_id in existing_tmdb_ids:
        return RuleResult(False, "already requested in Seerr")
    return RuleResult(True, "not yet requested in Seerr")


def rule_already_available_or_partial(
    item: WatchlistItem,
    seerr_available_ids: set[int],
    plex_library_ids: set[int],
    **_,
) -> RuleResult:
    """
    Skip if the item is already in Plex OR available/partial in Seerr.

    Priority:
    1. In Plex → SKIP (it's there and playable, regardless of Seerr status)
    2. Seerr available/partial + no Plex configured → SKIP (trust Seerr)
    3. Seerr available/partial + Plex configured but NOT in Plex → ALLOW
       (grabbed but not transferred yet — seedbox pending)
    """
    in_plex   = item.tmdb_id in plex_library_ids
    in_seerr  = item.tmdb_id in seerr_available_ids
    plex_on   = bool(plex_library_ids)

    # Already in Plex — skip it, we have it
    if plex_on and in_plex:
        return RuleResult(False, "already in Plex library")

    # Seerr says available/partial but not in Plex yet — seedbox pending transfer
    if in_seerr and plex_on and not in_plex:
        logger.debug("%s available in Seerr but not in Plex yet — allowing", item.title)
        return RuleResult(True, "available in Seerr but not in Plex yet")

    # Plex not configured — trust Seerr status only
    if not plex_on and in_seerr:
        return RuleResult(False, "available or partially available in Seerr")

    return RuleResult(True, "not available")


def rule_duplicate_in_session(
    item: WatchlistItem,
    seen_in_session: set[int],
    **_,
) -> RuleResult:
    """
    Skip if this exact item has already been ALLOW'd this session
    from another user's watchlist — forward it once, not multiple times.
    """
    if item.tmdb_id in seen_in_session:
        return RuleResult(False, "already being forwarded from another user's watchlist")
    return RuleResult(True, "no duplicate in current batch")


RuleFn = Callable[..., RuleResult]

RULE_REGISTRY: list[tuple[RuleFn, str]] = [
    (rule_block_list,                     "BLOCK"),
    (rule_already_requested_in_seerr,     "SKIP"),
    (rule_already_available_or_partial,   "SKIP"),
    (rule_duplicate_in_session,           "SKIP"),
]


class RulesEngine:
    def __init__(self, rules: list[tuple[RuleFn, str]] | None = None) -> None:
        self._rules = rules if rules is not None else RULE_REGISTRY

    def evaluate(
        self,
        item: WatchlistItem,
        *,
        ignore: IgnoreList,
        existing_tmdb_ids: set[int],
        seen_in_session: set[int],
        seerr_available_ids: set[int],
        plex_library_ids: set[int],
    ) -> tuple[str, str]:
        context = dict(
            ignore=ignore,
            existing_tmdb_ids=existing_tmdb_ids,
            seen_in_session=seen_in_session,
            seerr_available_ids=seerr_available_ids,
            plex_library_ids=plex_library_ids,
        )
        for rule_fn, fail_status in self._rules:
            result = rule_fn(item, **context)
            logger.debug(
                "Rule %-48s passed=%s  %s",
                rule_fn.__name__, result.passed, result.reason
            )
            if not result.passed:
                return fail_status, result.reason
        return "ALLOW", "all rules passed"
