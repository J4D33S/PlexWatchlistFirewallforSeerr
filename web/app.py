"""
web/app.py — FastAPI web UI for the Media Request Firewall.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import settings
from storage.ignore_list import ignore_list, _DB_PATH as IGNORE_DB
from storage.run_cache import load_last_run, save_last_run
from tmdb.posters import _DB_PATH as POSTER_DB

app = FastAPI(title="Media Request Firewall", version="2.0.0")

_HERE     = Path(__file__).parent
# Use project .env if it exists, otherwise parent folder .env
_PROJECT_ENV = Path(__file__).parent.parent / ".env"
_PARENT_ENV  = Path(__file__).parent.parent.parent / ".env"
_ENV_PATH    = _PROJECT_ENV if _PROJECT_ENV.exists() else _PARENT_ENV

templates = Jinja2Templates(directory=str(_HERE / "templates"))
app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

_last_run: dict = load_last_run()

# ── Scheduler ─────────────────────────────────────────────────────────────────

import asyncio

_scheduler_task: asyncio.Task | None = None


async def _scheduler_loop() -> None:
    """Background task that auto-runs the firewall on a schedule."""
    import logging
    log = logging.getLogger("scheduler")
    while True:
        interval = settings.schedule_interval
        if interval <= 0:
            await asyncio.sleep(60)  # check every minute if schedule gets enabled
            continue
        log.info("Scheduler: next run in %d hour(s)", interval)
        await asyncio.sleep(interval * 3600)
        log.info("Scheduler: running firewall automatically")
        global _last_run
        try:
            _last_run = _trigger_run()
        except Exception as exc:
            _last_run["error"] = str(exc)
            _last_run["ran_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_last_run(_last_run)
        log.info("Scheduler: run complete")


@app.on_event("startup")
async def startup():
    global _scheduler_task
    _scheduler_task = asyncio.create_task(_scheduler_loop())


# ── Env helpers ───────────────────────────────────────────────────────────────

def _read_env() -> dict[str, str]:
    if not _ENV_PATH.exists():
        return {}
    env: dict[str, str] = {}
    for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def _write_env(data: dict[str, str]) -> None:
    lines: list[str] = []
    written: set[str] = set()
    if _ENV_PATH.exists():
        for line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                lines.append(line)
            elif "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                lines.append(f"{k}={data[k]}" if k in data else line)
                if k in data:
                    written.add(k)
    for k, v in data.items():
        if k not in written:
            lines.append(f"{k}={v}")
    _ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── Run helper ────────────────────────────────────────────────────────────────

def _trigger_run() -> dict:
    from seerr.client import SeerrClient
    from engine.processor import Processor
    from tmdb.posters import get_poster_urls_bulk, get_anime_tmdb_ids

    logger = __import__("logging").getLogger("main")
    logger.info("Connecting to Seerr at: %s", settings.seerr_url)

    decisions = Processor(seerr_client=SeerrClient()).process_all_users()
    dicts      = [d.to_dict() for d in decisions]
    users_seen = sorted({d["item"]["added_by"] for d in dicts})

    # Fetch posters concurrently (cached after first run)
    poster_map = get_poster_urls_bulk([d["item"] for d in dicts])
    anime_ids  = get_anime_tmdb_ids()

    for d in dicts:
        tid = d["item"]["tmdb_id"]
        typ = d["item"]["type"]
        d["item"]["poster_url"] = (
            poster_map.get((tid, typ))
            or poster_map.get((tid, "tv"))
            or poster_map.get((tid, "movie"))
            or ""
        )
        if tid in anime_ids:
            d["item"]["type"] = "anime"

    summary = {s: sum(1 for d in dicts if d["status"] == s) for s in ("ALLOW", "BLOCK", "SKIP")}
    summary["TOTAL"] = len(dicts)

    return {
        "ran_at":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "decisions": dicts,
        "summary":   summary,
        "dry_run":   settings.dry_run,
        "users":     users_seen,
        "error":     None,
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request, name="dashboard.html",
        context={
            "last_run":         _last_run,
            "dry_run":          settings.dry_run,
            "schedule_interval": settings.schedule_interval,
        },
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="settings.html",
        context={"env": _read_env(), "saved": False},
    )


@app.post("/settings", response_class=HTMLResponse)
async def settings_save(
    request:           Request,
    DRY_RUN:           str = Form("true"),
    SEERR_URL:         str = Form(""),
    SEERR_API_KEY:     str = Form(""),
    PLEX_URL:          str = Form(""),
    PLEX_TOKEN:        str = Form(""),
    TVDB_API_KEY:      str = Form(""),
    TVDB_PIN:          str = Form(""),
    TMDB_API_KEY:      str = Form(""),
    SCHEDULE_INTERVAL: str = Form("0"),
    LOG_LEVEL:         str = Form("INFO"),
):
    env = _read_env()
    env.update({
        "DRY_RUN":           DRY_RUN,
        "SEERR_URL":         SEERR_URL.strip(),
        "SEERR_API_KEY":     SEERR_API_KEY.strip(),
        "PLEX_URL":          PLEX_URL.strip(),
        "PLEX_TOKEN":        PLEX_TOKEN.strip(),
        "TVDB_API_KEY":      TVDB_API_KEY.strip(),
        "TVDB_PIN":          TVDB_PIN.strip(),
        "TMDB_API_KEY":      TMDB_API_KEY.strip(),
        "SCHEDULE_INTERVAL": SCHEDULE_INTERVAL.strip(),
        "LOG_LEVEL":         LOG_LEVEL,
    })
    _write_env(env)
    return templates.TemplateResponse(
        request=request, name="settings.html",
        context={"env": _read_env(), "saved": True},
    )


@app.get("/ignore", response_class=HTMLResponse)
async def ignore_page(request: Request, q: str = Query(default="")):
    return templates.TemplateResponse(
        request=request, name="ignore.html",
        context={"entries": ignore_list.all_entries(search=q), "search": q, "count": ignore_list.count()},
    )


@app.post("/ignore/add-tmdb")
async def ignore_add_tmdb(
    tmdb_id:    int = Form(...),
    title:      str = Form(""),
    reason:     str = Form(""),
    poster_url: str = Form(""),
    tvdb_id:    int = Form(0),
    media_type: str = Form(""),
):
    ignore_list.add_tmdb(
        tmdb_id=tmdb_id, title=title, reason=reason,
        poster_url=poster_url, tvdb_id=tvdb_id, media_type=media_type,
    )
    return RedirectResponse("/ignore?added=1", status_code=303)


@app.post("/ignore/add-keyword")
async def ignore_add_keyword(keyword: str = Form(...), reason: str = Form("")):
    ignore_list.add_keyword(keyword=keyword, reason=reason)
    return RedirectResponse("/ignore?added=1", status_code=303)


@app.post("/ignore/remove")
async def ignore_remove(row_id: int = Form(...)):
    ignore_list.remove_by_id(row_id=row_id)
    return RedirectResponse("/ignore?removed=1", status_code=303)


@app.post("/run")
async def manual_run():
    global _last_run
    try:
        _last_run = _trigger_run()
    except Exception as exc:
        _last_run["error"] = str(exc)
        _last_run["ran_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_last_run(_last_run)
    return RedirectResponse("/", status_code=303)


@app.get("/api/status")
async def api_status():
    return {"status": "ok", "seerr_url": settings.seerr_url,
            "dry_run": settings.dry_run, "last_run": _last_run}


# ── TMDB search ───────────────────────────────────────────────────────────────

@app.get("/api/tmdb-search")
async def tmdb_search(q: str = Query(default=""), media_type: str = Query(default="all")):
    """Search TMDB — used by the block list search UI."""
    import requests as req

    api_key = settings.tmdb_api_key
    if not api_key:
        return {"results": [], "error": "TMDB API key not configured"}
    if not q.strip():
        return {"results": []}

    try:
        types = []
        if media_type in ("all", "movie"): types.append("movie")
        if media_type in ("all", "tv"):    types.append("tv")

        results = []
        for mtype in types:
            r = req.get(
                f"https://api.themoviedb.org/3/search/{mtype}",
                headers={"Authorization": f"Bearer {api_key}"},
                params={"query": q, "language": "en-US", "page": 1},
                timeout=10,
            )
            if r.status_code != 200:
                continue
            for item in r.json().get("results", [])[:8]:
                poster = item.get("poster_path", "")
                results.append({
                    "tmdb_id":    item["id"],
                    "title":      item.get("title") or item.get("name") or "",
                    "year":       (item.get("release_date") or item.get("first_air_date") or "")[:4],
                    "type":       mtype,
                    "poster_url": f"https://image.tmdb.org/t/p/w92{poster}" if poster else "",
                    "overview":   item.get("overview", "")[:150],
                })
        return {"results": results[:12]}
    except Exception as exc:
        return {"results": [], "error": str(exc)}


# ── Block list poster backfill ────────────────────────────────────────────────

@app.post("/ignore/fetch-posters")
async def fetch_missing_posters():
    """Backfill poster URLs for block list entries that don't have one."""
    import sqlite3
    import requests as req

    entries = ignore_list.all_entries()
    missing = [e for e in entries if e.get("tmdb_id") and not e.get("poster_url")]
    if not missing or not settings.tmdb_api_key:
        return RedirectResponse("/ignore", status_code=303)

    iconn = sqlite3.connect(str(IGNORE_DB))
    try:
        for e in missing:
            tid = e["tmdb_id"]
            url = _get_poster_for_id(tid, req)
            if url:
                iconn.execute("UPDATE ignore_list SET poster_url=? WHERE tmdb_id=?", (url, tid))
        iconn.commit()
    finally:
        iconn.close()
    return RedirectResponse("/ignore", status_code=303)


def _get_poster_for_id(tid: int, req) -> str:
    """Try to find a poster URL — check cache first, then TMDB directly."""
    import sqlite3
    # Check poster cache
    try:
        cconn = sqlite3.connect(str(POSTER_DB))
        cconn.row_factory = sqlite3.Row
        for mt in ("tv", "movie"):
            row = cconn.execute(
                "SELECT poster_url FROM poster_cache WHERE tmdb_id=? AND media_type=? AND poster_url!=''",
                (tid, mt)
            ).fetchone()
            if row:
                cconn.close()
                return row["poster_url"]
        cconn.close()
    except Exception:
        pass
    # Fetch from TMDB
    for endpoint in ("tv", "movie"):
        try:
            r = req.get(
                f"https://api.themoviedb.org/3/{endpoint}/{tid}",
                headers={"Authorization": f"Bearer {settings.tmdb_api_key}"},
                params={"language": "en-US"}, timeout=10,
            )
            if r.status_code == 200:
                path = r.json().get("poster_path", "")
                if path:
                    return f"https://image.tmdb.org/t/p/w200{path}"
        except Exception:
            pass
    return ""


# ── Cache management ──────────────────────────────────────────────────────────

@app.post("/settings/clear-poster-cache")
async def clear_poster_cache():
    from tmdb.posters import clear_cache as clear_tmdb
    from tvdb.client import clear_cache as clear_tvdb
    count = clear_tmdb() + clear_tvdb()
    return RedirectResponse(f"/settings?cleared={count}", status_code=303)


# ── Manual forward ────────────────────────────────────────────────────────────

@app.post("/forward/all")
async def forward_all():
    """Forward all ALLOW items to Seerr, bypassing dry_run."""
    from seerr.client import SeerrClient
    client = SeerrClient()
    allow_items = [d for d in _last_run.get("decisions", []) if d["status"] == "ALLOW"]
    forwarded = failed = 0
    for d in allow_items:
        resp = client.create_request(
            tmdb_id=d["item"]["tmdb_id"],
            media_type=d["item"]["type"],
            force=True,
        )
        if resp.get("error"):
            failed += 1
        else:
            forwarded += 1
    return RedirectResponse(f"/?forwarded={forwarded}&failed={failed}", status_code=303)


@app.post("/forward/one")
async def forward_one(
    tmdb_id: int = Form(...), media_type: str = Form(...), title: str = Form(""),
):
    """Forward a single item to Seerr, bypassing dry_run."""
    from seerr.client import SeerrClient
    resp = SeerrClient().create_request(tmdb_id=tmdb_id, media_type=media_type, force=True)
    if resp.get("error"):
        return RedirectResponse(f"/?forward_error={title}", status_code=303)
    return RedirectResponse(f"/?forwarded_one={title}", status_code=303)


@app.get("/api/tvdb-lookup")
async def tvdb_lookup(tvdb_id: int = Query(...)):
    """
    Look up a TVDB series ID and return its TMDB ID + metadata.
    Used by the block list 'Block by TVDB ID' form.
    """
    from tvdb.client import _get_token, _BASE, _TIMEOUT
    import requests as req

    token = _get_token()
    if not token:
        return {"error": "TVDB not configured — add TVDB_API_KEY and TVDB_PIN to Settings"}

    try:
        headers = {"Authorization": f"Bearer {token}"}
        resp = req.get(f"{_BASE}/series/{tvdb_id}/extended",
                       headers=headers, timeout=_TIMEOUT)
        if resp.status_code == 404:
            return {"error": f"TVDB series ID {tvdb_id} not found"}
        resp.raise_for_status()
        data   = resp.json().get("data", {}) or {}
        name   = data.get("name", "")
        image  = data.get("image", "")

        # Find TMDB remote ID
        tmdb_id = 0
        for rid in (data.get("remoteIds") or []):
            if rid.get("sourceName", "").lower() == "themoviedb.com":
                try:
                    tmdb_id = int(rid["id"])
                except Exception:
                    pass
                break

        if not tmdb_id:
            return {"error": f"Could not find TMDB ID for TVDB series {tvdb_id}. Try blocking by TMDB ID instead."}

        # Get poster from TVDB
        poster_url = ""
        if image:
            from tvdb.client import _IMG
            poster_url = image if image.startswith("http") else f"{_IMG}{image}"

        return {
            "tmdb_id":    tmdb_id,
            "tvdb_id":    tvdb_id,
            "title":      name,
            "poster_url": poster_url,
            "media_type": "tv",
        }
    except Exception as exc:
        return {"error": str(exc)}
