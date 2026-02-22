"""Release Radar plugin — auto-download movies hitting digital release.

Checks TMDB for movies that recently became available for digital
rental/purchase, filters out titles on the user's streaming services
(Disney+, Netflix, Hulu), then adds them to Radarr for automatic
download and import.

Tools:
- release_radar_check: Manually trigger a release check
- release_radar_history: Show auto-grabbed movies

Hooks:
- heartbeat_tick: Runs the daily check once per day (piggybacks on 15-min heartbeat)
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from server.plugins import PluginAPI

log = logging.getLogger("conduit.plugin.release-radar")

# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------
_TMDB_API_KEY = ""
_TMDB_BASE = "https://api.themoviedb.org/3"

_RADARR_URL = "http://localhost:7878"
_RADARR_API_KEY = ""

_MIN_RATING = 6.5
_MIN_POPULARITY = 50.0
_LOOKBACK_DAYS = 30
_CHECK_HOUR = 10  # Run daily check at this hour (local time)
_DATA_DIR = "~/.config/release-radar"
_ROOT_FOLDER = "/data/Movies"

# TMDB watch provider IDs for user's existing services
_EXCLUDED_PROVIDERS = {
    8,    # Netflix
    337,  # Disney+
    15,   # Hulu
}

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_db: _RadarDB | None = None
_last_check_date: str | None = None


# ---------------------------------------------------------------------------
# SQLite DB for tracking grabbed releases
# ---------------------------------------------------------------------------
class _RadarDB:
    def __init__(self, db_path: Path):
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS releases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tmdb_id INTEGER UNIQUE NOT NULL,
                title TEXT NOT NULL,
                year INTEGER,
                rating REAL,
                info_hash TEXT,
                torrent_name TEXT,
                radarr_id INTEGER,
                grabbed_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'grabbed'
            )"""
        )
        # Add radarr_id column if upgrading from old schema
        try:
            self._conn.execute("ALTER TABLE releases ADD COLUMN radarr_id INTEGER")
        except sqlite3.OperationalError:
            pass
        self._conn.commit()

    def has_movie(self, tmdb_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM releases WHERE tmdb_id = ?", (tmdb_id,)
        ).fetchone()
        return row is not None

    def add(self, tmdb_id: int, title: str, year: int | None,
            rating: float, radarr_id: int | None = None) -> None:
        self._conn.execute(
            """INSERT OR IGNORE INTO releases
               (tmdb_id, title, year, rating, radarr_id, torrent_name, grabbed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (tmdb_id, title, year, rating, radarr_id,
             f"Added to Radarr (auto)",
             datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def list_recent(self, limit: int = 30) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM releases ORDER BY grabbed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def _get_db() -> _RadarDB:
    global _db
    if _db is None:
        data_dir = Path(_DATA_DIR).expanduser()
        data_dir.mkdir(parents=True, exist_ok=True)
        _db = _RadarDB(data_dir / "radar.db")
    return _db


# ---------------------------------------------------------------------------
# TMDB API
# ---------------------------------------------------------------------------
async def _tmdb_get(endpoint: str, params: dict | None = None) -> dict:
    url = f"{_TMDB_BASE}{endpoint}"
    p = dict(params or {})
    p["api_key"] = _TMDB_API_KEY
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=p)
        resp.raise_for_status()
        return resp.json()


async def _discover_new_digital_releases() -> list[dict]:
    """Find movies that recently hit digital release in the US."""
    today = datetime.now().strftime("%Y-%m-%d")
    lookback = (datetime.now() - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    params = {
        "region": "US",
        "with_release_type": "4|5",  # Digital or Physical
        "release_date.gte": lookback,
        "release_date.lte": today,
        "vote_average.gte": str(_MIN_RATING),
        "vote_count.gte": "100",
        "sort_by": "popularity.desc",
        "with_original_language": "en",
        "page": "1",
    }

    data = await _tmdb_get("/discover/movie", params)
    movies = data.get("results", [])
    movies = [m for m in movies if m.get("popularity", 0) >= _MIN_POPULARITY]
    return movies


async def _get_watch_providers(tmdb_id: int) -> dict:
    data = await _tmdb_get(f"/movie/{tmdb_id}/watch/providers")
    return data.get("results", {}).get("US", {})


async def _is_on_excluded_service(tmdb_id: int) -> bool:
    providers = await _get_watch_providers(tmdb_id)
    flatrate = providers.get("flatrate", [])
    for p in flatrate:
        if p.get("provider_id") in _EXCLUDED_PROVIDERS:
            return True
    return False


# ---------------------------------------------------------------------------
# Radarr API
# ---------------------------------------------------------------------------
async def _radarr_get(endpoint: str, params: dict | None = None) -> dict | list:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{_RADARR_URL}/api/v3{endpoint}",
            params=params,
            headers={"X-Api-Key": _RADARR_API_KEY},
        )
        resp.raise_for_status()
        return resp.json()


async def _radarr_post(endpoint: str, data: dict) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{_RADARR_URL}/api/v3{endpoint}",
            json=data,
            headers={"X-Api-Key": _RADARR_API_KEY},
        )
        resp.raise_for_status()
        return resp.json()


async def _radarr_add_movie(tmdb_id: int, title: str, year: int | None) -> dict | None:
    """Add a movie to Radarr with automatic search. Returns movie dict or None."""
    # Get quality profile
    profiles = await _radarr_get("/qualityprofile")
    profile_id = next(
        (p["id"] for p in profiles if p["name"] == "HD-1080p"),
        profiles[0]["id"] if profiles else 1,
    )

    payload = {
        "tmdbId": tmdb_id,
        "title": title,
        "year": year or 0,
        "rootFolderPath": _ROOT_FOLDER,
        "qualityProfileId": profile_id,
        "monitored": True,
        "addOptions": {
            "searchForMovie": True,
        },
    }

    try:
        return await _radarr_post("/movie", payload)
    except httpx.HTTPStatusError as e:
        # 400 typically means already added
        if e.response.status_code == 400:
            log.debug("Release Radar: %s already in Radarr", title)
            return None
        raise


async def _radarr_has_movie(tmdb_id: int) -> bool:
    """Check if a movie is already in Radarr."""
    movies = await _radarr_get("/movie")
    return any(m.get("tmdbId") == tmdb_id for m in movies)


# ---------------------------------------------------------------------------
# Core check logic
# ---------------------------------------------------------------------------
async def _run_check() -> list[dict]:
    """Run a full release radar check. Returns list of grabbed movies."""
    if not _TMDB_API_KEY:
        log.warning("Release Radar: TMDB API key not configured")
        return []
    if not _RADARR_API_KEY:
        log.warning("Release Radar: Radarr API key not configured")
        return []

    db = _get_db()
    grabbed = []

    # 1. Discover new digital releases
    try:
        movies = await _discover_new_digital_releases()
    except Exception as e:
        log.error("Release Radar: TMDB discover failed: %s", e)
        return []

    log.info("Release Radar: Found %d candidate movies from TMDB", len(movies))

    for movie in movies:
        tmdb_id = movie["id"]
        title = movie.get("title", "Unknown")
        year_str = movie.get("release_date", "")[:4]
        year = int(year_str) if year_str.isdigit() else None
        rating = movie.get("vote_average", 0)

        # Skip if already in our DB
        if db.has_movie(tmdb_id):
            continue

        # Check if on excluded streaming services
        try:
            if await _is_on_excluded_service(tmdb_id):
                log.debug("Release Radar: Skipping %s — on excluded service", title)
                continue
        except Exception as e:
            log.debug("Release Radar: Provider check failed for %s: %s", title, e)

        # Check if already in Radarr
        try:
            if await _radarr_has_movie(tmdb_id):
                log.debug("Release Radar: %s already in Radarr, recording", title)
                db.add(tmdb_id=tmdb_id, title=title, year=year, rating=rating)
                continue
        except Exception as e:
            log.debug("Release Radar: Radarr check failed for %s: %s", title, e)

        # Add to Radarr
        try:
            result = await _radarr_add_movie(tmdb_id, title, year)
        except Exception as e:
            log.warning("Release Radar: Radarr add failed for %s: %s", title, e)
            continue

        radarr_id = result.get("id") if result else None
        log.info("Release Radar: Added %s to Radarr (id=%s)", title, radarr_id)

        db.add(
            tmdb_id=tmdb_id,
            title=title,
            year=year,
            rating=rating,
            radarr_id=radarr_id,
        )
        grabbed.append({
            "title": title,
            "year": year_str,
            "rating": rating,
            "radarr_id": radarr_id,
        })

    return grabbed


# ---------------------------------------------------------------------------
# Notification helper
# ---------------------------------------------------------------------------
async def _notify_grabs(grabbed: list[dict]) -> None:
    if not grabbed:
        return
    try:
        from server import ntfy
        lines = [f"Release Radar added {len(grabbed)} movie(s) to Radarr:"]
        for g in grabbed:
            lines.append(f"- {g['title']} ({g['year']}) [{g['rating']}/10]")
        await ntfy.push(
            title="Release Radar",
            body="\n".join(lines),
            tags=["movie_camera", "robot"],
        )
    except Exception as e:
        log.debug("Release Radar: ntfy notification failed: %s", e)


# ---------------------------------------------------------------------------
# Heartbeat hook — daily check
# ---------------------------------------------------------------------------
async def _on_heartbeat(**kwargs) -> dict | None:
    global _last_check_date

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    if _last_check_date == today:
        return None
    if now.hour < _CHECK_HOUR:
        return None

    _last_check_date = today
    log.info("Release Radar: Starting daily check")

    try:
        grabbed = await _run_check()
        if grabbed:
            log.info("Release Radar: Added %d movies to Radarr", len(grabbed))
            await _notify_grabs(grabbed)
        else:
            log.info("Release Radar: No new movies to add today")
    except Exception as e:
        log.error("Release Radar: Daily check failed: %s", e)

    return None


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------
async def _release_radar_check() -> str:
    if not _TMDB_API_KEY:
        return "Error: RELEASE_RADAR_TMDB_KEY not configured."
    if not _RADARR_API_KEY:
        return "Error: HUNTER_RADARR_API_KEY not configured."

    try:
        grabbed = await _run_check()
    except Exception as e:
        return f"Error running release check: {e}"

    if not grabbed:
        return ("Release Radar: No new movies to add. Either nothing new hit "
                "digital release, movies are on your streaming services "
                "(Disney+, Netflix, Hulu), or they're already in Radarr.")

    lines = [f"Release Radar added {len(grabbed)} movie(s) to Radarr:\n"]
    for g in grabbed:
        lines.append(
            f"- **{g['title']}** ({g['year']}) — {g['rating']}/10"
        )

    await _notify_grabs(grabbed)
    return "\n".join(lines)


async def _release_radar_history(limit: int = 30) -> str:
    db = _get_db()
    entries = db.list_recent(limit)

    if not entries:
        return "No release radar history yet."

    lines = [f"Last {len(entries)} auto-added movies:\n"]
    for e in entries:
        grabbed = e["grabbed_at"][:10]
        year = e["year"] or "?"
        lines.append(f"- {e['title']} ({year}) — {e['rating']}/10 — added {grabbed}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def register(api: PluginAPI):
    global _TMDB_API_KEY, _RADARR_URL, _RADARR_API_KEY
    global _MIN_RATING, _MIN_POPULARITY
    global _LOOKBACK_DAYS, _CHECK_HOUR, _DATA_DIR, _ROOT_FOLDER

    # TMDB
    _TMDB_API_KEY = (api.config.get("tmdb_api_key")
                     or os.environ.get("RELEASE_RADAR_TMDB_KEY", ""))

    # Radarr (shared with hunter plugin)
    _RADARR_URL = (api.config.get("radarr_url")
                   or os.environ.get("HUNTER_RADARR_URL", _RADARR_URL))
    _RADARR_API_KEY = (api.config.get("radarr_api_key")
                       or os.environ.get("HUNTER_RADARR_API_KEY", _RADARR_API_KEY))

    # Tuning
    _MIN_RATING = float(os.environ.get("RELEASE_RADAR_MIN_RATING", _MIN_RATING))
    _MIN_POPULARITY = float(os.environ.get("RELEASE_RADAR_MIN_POPULARITY", _MIN_POPULARITY))
    _LOOKBACK_DAYS = int(os.environ.get("RELEASE_RADAR_LOOKBACK_DAYS", _LOOKBACK_DAYS))
    _CHECK_HOUR = int(os.environ.get("RELEASE_RADAR_CHECK_HOUR", _CHECK_HOUR))
    _DATA_DIR = os.environ.get("RELEASE_RADAR_DATA_DIR", _DATA_DIR)
    _ROOT_FOLDER = os.environ.get("RELEASE_RADAR_ROOT_FOLDER", _ROOT_FOLDER)

    if not _TMDB_API_KEY:
        api.log("RELEASE_RADAR_TMDB_KEY not set — auto-check disabled",
                level="warning")
    if not _RADARR_API_KEY:
        api.log("HUNTER_RADARR_API_KEY not set — Radarr integration disabled",
                level="warning")

    # -- release_radar_check --
    api.register_tool(
        name="release_radar_check",
        description=(
            "Check for new movies that recently hit digital release and are NOT "
            "on the user's streaming services. Adds qualifying movies to Radarr "
            "for automatic download. Runs automatically once daily."
        ),
        parameters={"type": "object", "properties": {}},
        handler=_release_radar_check,
        permission="none",
    )

    # -- release_radar_history --
    api.register_tool(
        name="release_radar_history",
        description=(
            "Show movies that were automatically added to Radarr by the release radar."
        ),
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max entries to show (default: 30).",
                    "default": 30,
                },
            },
        },
        handler=_release_radar_history,
        permission="none",
    )

    # -- heartbeat hook for daily auto-check --
    api.register_hook("heartbeat_tick", _on_heartbeat)

    tool_count = 2
    api.log(f"Loaded plugin release-radar — {tool_count} tools, daily check at {_CHECK_HOUR}:00")
