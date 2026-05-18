"""
utils/logger.py — Clean, consistent logging for the media firewall.
"""

import logging
import sys
from typing import Literal

from config import settings

# ── ANSI colour codes ───────────────────────────────────────────────────────
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"
_GREY   = "\033[90m"
_BLUE   = "\033[94m"


class _ColourFormatter(logging.Formatter):
    """Apply per-level colours to console output."""

    _LEVEL_COLOURS = {
        logging.DEBUG:    _GREY,
        logging.INFO:     _CYAN,
        logging.WARNING:  _YELLOW,
        logging.ERROR:    _RED,
        logging.CRITICAL: _RED + _BOLD,
    }

    def format(self, record: logging.LogRecord) -> str:
        colour = self._LEVEL_COLOURS.get(record.levelno, "")
        record.levelname = f"{colour}{record.levelname:<8}{_RESET}"
        return super().format(record)


def get_logger(name: str = "media-firewall") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:          # already configured → return as-is
        return logger

    logger.setLevel(settings.log_level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        _ColourFormatter(
            fmt="%(asctime)s  %(levelname)s %(name)s — %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger


# ── Decision-banner helpers ─────────────────────────────────────────────────

DecisionStatus = Literal["ALLOW", "BLOCK", "SKIP"]


def log_decision(
    logger: logging.Logger,
    status: DecisionStatus,
    title: str,
    reason: str,
    dry_run: bool = False,
) -> None:
    """Print a highly-visible one-liner for each firewall decision."""
    colour_map: dict[str, str] = {
        "ALLOW": _GREEN,
        "BLOCK": _RED,
        "SKIP":  _YELLOW,
    }
    colour = colour_map.get(status, "")
    tag = f"{colour}{_BOLD}[{status}]{_RESET}"
    dry = f"  {_BLUE}(DRY RUN — no request sent){_RESET}" if (dry_run and status == "ALLOW") else ""
    logger.info("%s  %s%s  — %s", tag, _BOLD + title + _RESET, dry, reason)


def log_banner(logger: logging.Logger, text: str) -> None:
    """Print a section separator banner."""
    bar = "─" * 60
    logger.info("%s%s%s", _CYAN, bar, _RESET)
    logger.info("%s  %s%s", _CYAN + _BOLD, text, _RESET)
    logger.info("%s%s%s", _CYAN, bar, _RESET)
