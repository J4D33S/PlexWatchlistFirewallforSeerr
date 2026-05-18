# 🔥 Media Request Firewall

A **policy-driven pre-request engine** that replaces Seerr's native Plex watchlist sync — giving you full control over what actually gets requested.

Instead of every watchlist item automatically becoming a Seerr request, the firewall filters, deduplicates, and decides what passes through. Run it in **dry-run mode** to preview decisions before anything is sent.

```
Plex Watchlists ──► Firewall Engine ──► Seerr API ──► Radarr / Sonarr / Plex
```

---

## Quick Start

### Prerequisites
- Docker + Docker Compose (Docker Desktop on Windows)
- A running Seerr instance
- Seerr API key (Settings → General → API Key)

### Setup

**Linux:**
```bash
git clone https://github.com/J4D33S/PlexWatchlistFirewallforSeerr.git media-firewall
cd media-firewall
cp .env.example .env
nano .env   # fill in your values
docker compose up -d
```

**Windows:**
```powershell
# Clone into a parent folder e.g. DockerTest
git clone https://github.com/J4D33S/PlexWatchlistFirewallforSeerr.git media-firewall

# Create your .env in the PARENT folder (not inside media-firewall)
copy media-firewall\.env.example .env
notepad .env   # fill in your values

# Run the updater — it copies keys and starts the container
Unblock-File -Path media-firewall\update.ps1
.\media-firewall\update.ps1
```

### First run

1. Open **http://localhost:7878/settings** and verify your Seerr URL + API key
2. Click **Run Firewall** on the dashboard
3. Review the decisions — everything is in dry-run mode, nothing is sent yet
4. When you're happy with what you see, turn off Seerr's native watchlist sync and set `DRY_RUN=false`

---

## How it works

On each run the firewall:

1. **Syncs Seerr's blocklist** into its own block list
2. **Fetches all user watchlists** via Seerr's API (users must be logged into Seerr with their Plex account)
3. **Fetches your Plex library** to know what's already there
4. **Runs each item through rules** in priority order:

| Rule | Result |
|------|--------|
| On the block list (ID or keyword) | BLOCK |
| Already requested in Seerr | SKIP |
| Already in your Plex library | SKIP |
| Available/partial in Seerr | SKIP |
| Duplicate across user watchlists | SKIP |
| Everything else | ALLOW |

5. **Forwards ALLOW items** to Seerr as requests (or logs them in dry-run)

---

## Web UI

| Page | URL | Purpose |
|------|-----|---------|
| Dashboard | `/` | Run firewall, view decisions, filter by user/status/type |
| Block List | `/ignore` | Manage blocked titles — search TMDB/TVDB, add by ID or keyword |
| Settings | `/settings` | Configure Seerr, Plex, TMDB, TVDB connections |

---

## Configuration

Copy `.env.example` to `.env` and fill in your values:

| Variable | Required | Description |
|----------|----------|-------------|
| `DRY_RUN` | Yes | `true` = preview only, `false` = send real requests |
| `SEERR_URL` | Yes | Your Seerr address (use LAN IP in Docker, not localhost) |
| `SEERR_API_KEY` | Yes | Seerr → Settings → General → API Key |
| `PLEX_URL` | Recommended | Your Plex server address |
| `PLEX_TOKEN` | Recommended | Your Plex authentication token |
| `TVDB_API_KEY` | Optional | For TV show posters (thetvdb.com/api-information) |
| `TVDB_PIN` | Optional | TVDB user PIN (required with API key) |
| `TMDB_API_KEY` | Optional | For movie posters + anime detection (themoviedb.org) |
| `LOG_LEVEL` | No | `DEBUG` / `INFO` / `WARNING` / `ERROR` (default: INFO) |

> ⚠️ **Docker networking:** Do not use `localhost` for Seerr or Plex URLs — use your machine's LAN IP (e.g. `192.168.1.100`) or a domain name.

---

## Updating

**Linux:**
```bash
cd media-firewall
git pull
docker compose down
docker compose up -d --build
```

**Windows:**

The `.env` file lives in the **parent folder** (`DockerTest/.env`), not inside `media-firewall/`. This prevents it from being overwritten when you pull updates.

```
DockerTest/
├── .env          ← your keys live here, never touched by updates
└── media-firewall/
    └── ...
```

After pulling, run `update.ps1` (right-click → Run with PowerShell). It copies the parent `.env` into the project folder and rebuilds the container automatically.

---

## Architecture

```
media-firewall/
├── config.py              # Settings loader (reads .env fresh every access)
├── main.py                # CLI entry point
│
├── seerr/client.py        # Seerr REST API wrapper
├── plex/
│   ├── watchlist.py       # Fetch user watchlists via Seerr API
│   └── library.py         # Fetch Plex library TMDB IDs
│
├── engine/
│   ├── rules.py           # Individual rule functions + registry
│   └── processor.py       # Decision pipeline
│
├── storage/
│   ├── ignore_list.py     # SQLite block list (TMDB ID, TVDB ID, keyword)
│   └── run_cache.py       # Persist last run across restarts
│
├── tmdb/posters.py        # TMDB poster fetching (concurrent, cached)
├── tvdb/client.py         # TVDB poster fetching (cached, token auto-refresh)
│
└── web/
    ├── app.py             # FastAPI web UI
    └── templates/         # Jinja2 HTML templates
```

---

## Requirements

- Python 3.12+
- Docker + Docker Compose
- Seerr (tested with Seerr — github.com/seerr-team/seerr)

---

## License

MIT — do whatever you want with it.

---

## Contributing

PRs welcome. Planned future features:
- Scheduled auto-run (every N hours)
- Per-user rule sets
- Discord webhook notifications
- Run history / audit log
