"""
main.py — Media Request Firewall · CLI entry point

Fetches real watchlists from all Seerr users and runs them
through the rule engine. DRY_RUN=true by default.

Run:
    python main.py
    python main.py --json
"""

from __future__ import annotations

import json
import sys

from config import settings
from engine.processor import Processor
from seerr.client import SeerrClient
from utils.logger import get_logger, log_banner

logger = get_logger("main")


def run() -> list[dict]:
    log_banner(logger, f"Media Request Firewall  |  DRY_RUN={settings.dry_run}")

    client    = SeerrClient()
    processor = Processor(seerr_client=client)

    log_banner(logger, "Fetching user watchlists + processing")
    decisions = processor.process_all_users()

    allow = sum(1 for d in decisions if d.status == "ALLOW")
    block = sum(1 for d in decisions if d.status == "BLOCK")
    skip  = sum(1 for d in decisions if d.status == "SKIP")

    log_banner(logger, "SUMMARY")
    logger.info("  ALLOW  %d", allow)
    logger.info("  BLOCK  %d", block)
    logger.info("  SKIP   %d", skip)
    logger.info("  TOTAL  %d", len(decisions))

    if settings.dry_run:
        logger.info("")
        logger.info("  ⚠  DRY RUN — no requests were sent to Seerr")

    return [d.to_dict() for d in decisions]


def main() -> None:
    results = run()
    if "--json" in sys.argv:
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
