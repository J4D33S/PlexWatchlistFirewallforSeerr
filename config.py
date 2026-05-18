"""
config.py — Settings loader.
Reads fresh from .env on every property access — no caching.
"""

import os
from pathlib import Path

_ENV_PATH = Path(__file__).parent / ".env"


def _bool(val: str | None, default: bool = False) -> bool:
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _read_env_file() -> dict[str, str]:
    """Parse .env file into a plain dict, fresh on every call."""
    if not _ENV_PATH.exists():
        return {}
    env: dict[str, str] = {}
    for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


class Settings:
    """Reads config fresh from .env on every property access. No caching."""

    def _get(self, key: str, default: str = "") -> str:
        # Environment variables take priority, but ignore placeholder values
        val = os.environ.get(key, "")
        if val and val != f"your_{key.lower()}_here":
            return val
        return _read_env_file().get(key, default)

    @property
    def dry_run(self) -> bool:
        return _bool(self._get("DRY_RUN", "true"), default=True)

    @property
    def seerr_url(self) -> str:
        return self._get("SEERR_URL", "").rstrip("/")

    @property
    def seerr_api_key(self) -> str:
        return self._get("SEERR_API_KEY", "")

    @property
    def plex_url(self) -> str:
        return self._get("PLEX_URL", "").rstrip("/")

    @property
    def plex_token(self) -> str:
        return self._get("PLEX_TOKEN", "")

    @property
    def tvdb_api_key(self) -> str:
        return self._get("TVDB_API_KEY", "")

    @property
    def tvdb_pin(self) -> str:
        return self._get("TVDB_PIN", "")

    @property
    def tmdb_api_key(self) -> str:
        return self._get("TMDB_API_KEY", "")

    @property
    def log_level(self) -> str:
        return self._get("LOG_LEVEL", "INFO").upper()

    @property
    def schedule_interval(self) -> int:
        """Auto-run interval in hours. 0 = disabled."""
        try:
            return int(self._get("SCHEDULE_INTERVAL", "0"))
        except ValueError:
            return 0


settings = Settings()
