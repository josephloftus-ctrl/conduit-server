"""Release Radar plugin — auto-download movies hitting digital release.

Checks TMDB for movies that recently became available for digital
rental/purchase, filters out titles on the user's streaming services
(Disney+, Netflix, Hulu), then searches Jackett and downloads the best
match via qBittorrent.

Tools:
- release_radar_check: Manually trigger a release check
- release_radar_history: Show auto-grabbed movies

Hooks:
- heartbeat_tick: Runs the daily check once per day (piggybacks on 15-min heartbeat)
"""

from __future__ import annotations

import logging
import os
import re
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

_JACKETT_URL = "http://localhost:9117"
_JACKETT_API_KEY = ""
_QBIT_URL = "http://localhost:8080"
_QBIT_USER = "admin"
_QBIT_PASS = "adminadmin"

_MIN_RATING = 6.5
_MIN_POPULARITY = 50.0
_MIN_SEEDERS = 5
_LOOKBACK_DAYS = 30
_CHECK_HOUR = 10  # Run daily check at this hour (local time)
_DATA_DIR = "~/.config/release-radar"

# TMDB watch provider IDs for user's existing services
_EXCLUDED_PROVIDERS = {
    8,    # Netflix
    337,  # Disney+
    15,   # Hulu
}

_TORZNAB_NS = "http://torznab.com/schemas/2015/feed"

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_qbit_sid: str | None = None
_db: _RadarDB | None = None
_last_check_date: str | None = None

# ---------------------------------------------------------------------------
# Quality scoring regex patterns
# ---------------------------------------------------------------------------
_QUALITY_PATTERNS = [
    (re.compile(r"2160p|4K|UHD", re.I), 4),
    (re.compile(r"1080p", re.I), 3),
    (re.compile(r"720p", re.I), 1),
    (re.compile(r"REMUX", re.I), 3),
    (re.compile(r"HDR|HDR10|HDR10\+|Dolby\.?Vision|DV\b", re.I), 2),
    (re.compile(r"x265|HEVC|H\.?265", re.I), 1),
    (re.compile(r"BluRay|Blu-Ray", re.I), 1),
]

# Negative patterns — avoid cams, screeners, etc.
_BAD_PATTERNS = [
    re.compile(r"CAM|HDCAM|TS|TELESYNC|TC|TELECINE|SCR|SCREENER|DVDSCR", re.I),
    re.compile(r"HDTS|HDTC|WEB-?Scr", re.I),
]


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
                grabbed_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'grabbed'
            )"""
        )
        self._conn.commit()

    def has_movie(self, tmdb_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM releases WHERE tmdb_id = ?", (tmdb_id,)
        ).fetchone()
        return row is not None

    def add(self, tmdb_id: int, title: str, year: int | None,
            rating: float, info_hash: str | None, torrent_name: str) -> None:
        self._conn.execute(
            """INSERT OR IGNORE INTO releases
               (tmdb_id, title, year, rating, info_hash, torrent_name, grabbed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (tmdb_id, title, year, rating, info_hash, torrent_name,
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
    """Make an authenticated TMDB API request."""
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

    # Filter by popularity threshold
    movies = [m for m in movies if m.get("popularity", 0) >= _MIN_POPULARITY]

    return movies


async def _get_watch_providers(tmdb_id: int) -> dict:
    """Get watch providers for a movie in the US."""
    data = await _tmdb_get(f"/movie/{tmdb_id}/watch/providers")
    return data.get("results", {}).get("US", {})


async def _is_on_excluded_service(tmdb_id: int) -> bool:
    """Check if a movie is available on one of the user's streaming services."""
    providers = await _get_watch_providers(tmdb_id)

    # Check flatrate (subscription streaming) providers
    flatrate = providers.get("flatrate", [])
    for p in flatrate:
        if p.get("provider_id") in _EXCLUDED_PROVIDERS:
            return True

    return False


# ---------------------------------------------------------------------------
# Jackett / Torznab (reuses pattern from hunter plugin)
# ---------------------------------------------------------------------------
def _parse_torznab(xml_text: str) -> list[dict]:
    root = ElementTree.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        return []

    results = []
    for item in channel.findall("item"):
        title = item.findtext("title", "")
        link = item.findtext("link", "")
        size_text = item.findtext("size", "0")
        source = item.findtext("jackettindexer", "unknown")

        seeders = 0
        info_hash = None
        magnet = None

        for attr in item.findall(f"{{{_TORZNAB_NS}}}attr"):
            name = attr.get("name", "")
            value = attr.get("value", "")
            if name == "seeders":
                seeders = int(value)
            elif name == "infohash":
                info_hash = value
            elif name == "magneturl":
                magnet = value

        if not magnet and link.startswith("magnet:"):
            magnet = link

        if not info_hash and magnet:
            m = re.search(r"btih:([a-fA-F0-9]{40})", magnet)
            if m:
                info_hash = m.group(1)

        results.append({
            "title": title,
            "magnet": magnet,
            "link": link,
            "size": int(size_text) if size_text.isdigit() else 0,
            "seeders": seeders,
            "info_hash": info_hash,
            "source": source,
        })

    return results


def _score_result(title: str) -> int:
    """Score a torrent result by quality indicators."""
    # Reject bad quality
    for pat in _BAD_PATTERNS:
        if pat.search(title):
            return -100

    score = 0
    for pat, points in _QUALITY_PATTERNS:
        if pat.search(title):
            score += points
    return score


async def _search_jackett(query: str) -> list[dict]:
    """Search Jackett for a movie, return results sorted by quality + seeders."""
    if not _JACKETT_API_KEY:
        return []

    params = {
        "apikey": _JACKETT_API_KEY,
        "t": "search",
        "q": query,
        "cat": "2000",  # Movies
    }
    url = f"{_JACKETT_URL}/api/v2.0/indexers/all/results/torznab/api"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()

    results = _parse_torznab(resp.text)

    # Filter: need magnet, min seeders, no bad quality
    results = [
        r for r in results
        if r.get("magnet")
        and r["seeders"] >= _MIN_SEEDERS
        and _score_result(r["title"]) >= 0
    ]

    # Sort by quality score (desc), then seeders (desc)
    results.sort(key=lambda r: (_score_result(r["title"]), r["seeders"]), reverse=True)

    return results


# ---------------------------------------------------------------------------
# qBittorrent API (reuses pattern from hunter plugin)
# ---------------------------------------------------------------------------
async def _qbit_login(client: httpx.AsyncClient) -> str | None:
    global _qbit_sid
    resp = await client.post(
        f"{_QBIT_URL}/api/v2/auth/login",
        data={"username": _QBIT_USER, "password": _QBIT_PASS},
        headers={"Referer": _QBIT_URL},
    )
    if resp.text.strip() == "Ok.":
        sid = resp.cookies.get("SID")
        if sid:
            _qbit_sid = sid
            return sid
    return None


async def _qbit_add(magnet: str, category: str = "movies") -> bool:
    global _qbit_sid
    url = f"{_QBIT_URL}/api/v2/torrents/add"
    headers = {"Referer": _QBIT_URL}
    data = {"urls": magnet, "category": category}

    async with httpx.AsyncClient(timeout=15) as client:
        if _qbit_sid:
            client.cookies.set("SID", _qbit_sid)

        resp = await client.post(url, headers=headers, data=data)

        if resp.status_code == 403:
            sid = await _qbit_login(client)
            if not sid:
                return False
            client.cookies.set("SID", sid)
            resp = await client.post(url, headers=headers, data=data)

        return resp.status_code == 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _human_size(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(nbytes) < 1024:
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024  # type: ignore[assignment]
    return f"{nbytes:.1f} PB"


# Need the import for _parse_torznab
from xml.etree import ElementTree


# ---------------------------------------------------------------------------
# Core check logic
# ---------------------------------------------------------------------------
async def _run_check() -> list[dict]:
    """Run a full release radar check. Returns list of grabbed movies."""
    if not _TMDB_API_KEY:
        log.warning("Release Radar: TMDB API key not configured")
        return []
    if not _JACKETT_API_KEY:
        log.warning("Release Radar: Jackett API key not configured")
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
        year = movie.get("release_date", "")[:4]
        rating = movie.get("vote_average", 0)

        # Skip if already grabbed
        if db.has_movie(tmdb_id):
            continue

        # Check if on excluded streaming services
        try:
            if await _is_on_excluded_service(tmdb_id):
                log.debug("Release Radar: Skipping %s — on excluded service", title)
                continue
        except Exception as e:
            log.debug("Release Radar: Provider check failed for %s: %s", title, e)
            # Continue anyway — better to grab than skip on API error

        # Search Jackett
        search_query = f"{title} {year}" if year else title
        try:
            results = await _search_jackett(search_query)
        except Exception as e:
            log.warning("Release Radar: Jackett search failed for %s: %s", title, e)
            continue

        if not results:
            log.debug("Release Radar: No good results for %s", title)
            continue

        # Take the best result
        best = results[0]
        log.info(
            "Release Radar: Grabbing %s — %s (%d seeds, score %d)",
            title, best["title"], best["seeders"], _score_result(best["title"]),
        )

        # Download
        try:
            ok = await _qbit_add(best["magnet"])
        except Exception as e:
            log.warning("Release Radar: qBit add failed for %s: %s", title, e)
            continue

        if ok:
            db.add(
                tmdb_id=tmdb_id,
                title=title,
                year=int(year) if year.isdigit() else None,
                rating=rating,
                info_hash=best.get("info_hash"),
                torrent_name=best["title"],
            )
            grabbed.append({
                "title": title,
                "year": year,
                "rating": rating,
                "torrent": best["title"],
                "size": best["size"],
                "seeders": best["seeders"],
            })

    return grabbed


# ---------------------------------------------------------------------------
# Notification helper
# ---------------------------------------------------------------------------
async def _notify_grabs(grabbed: list[dict]) -> None:
    """Send ntfy notification for grabbed movies."""
    if not grabbed:
        return

    try:
        from server import ntfy
        lines = [f"Release Radar grabbed {len(grabbed)} movie(s):"]
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
    """Called every ~15 minutes by the heartbeat system. Runs check once daily."""
    global _last_check_date

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # Only run once per day, at or after the configured hour
    if _last_check_date == today:
        return None
    if now.hour < _CHECK_HOUR:
        return None

    _last_check_date = today
    log.info("Release Radar: Starting daily check")

    try:
        grabbed = await _run_check()
        if grabbed:
            log.info("Release Radar: Grabbed %d movies", len(grabbed))
            await _notify_grabs(grabbed)
        else:
            log.info("Release Radar: No new movies to grab today")
    except Exception as e:
        log.error("Release Radar: Daily check failed: %s", e)

    return None


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------
async def _release_radar_check() -> str:
    """Manually trigger a release radar check."""
    if not _TMDB_API_KEY:
        return "Error: RELEASE_RADAR_TMDB_KEY not configured. Get a free API key at https://www.themoviedb.org/settings/api"

    try:
        grabbed = await _run_check()
    except Exception as e:
        return f"Error running release check: {e}"

    if not grabbed:
        return "Release Radar: No new movies to grab. Either nothing new hit digital release, movies are on your streaming services (Disney+, Netflix, Hulu), or they've already been grabbed."

    lines = [f"Release Radar grabbed {len(grabbed)} movie(s):\n"]
    for g in grabbed:
        size = _human_size(g["size"]) if g["size"] else "unknown"
        lines.append(
            f"- **{g['title']}** ({g['year']}) — {g['rating']}/10\n"
            f"  {g['torrent']} | {size} | {g['seeders']} seeds"
        )

    await _notify_grabs(grabbed)
    return "\n".join(lines)


async def _release_radar_history(limit: int = 30) -> str:
    """Show movies grabbed by release radar."""
    db = _get_db()
    entries = db.list_recent(limit)

    if not entries:
        return "No release radar history yet."

    lines = [f"Last {len(entries)} auto-grabbed movies:\n"]
    for e in entries:
        grabbed = e["grabbed_at"][:10]
        year = e["year"] or "?"
        lines.append(f"- {e['title']} ({year}) — {e['rating']}/10 — grabbed {grabbed}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def register(api: PluginAPI):
    global _TMDB_API_KEY, _JACKETT_URL, _JACKETT_API_KEY
    global _QBIT_URL, _QBIT_USER, _QBIT_PASS
    global _MIN_RATING, _MIN_POPULARITY, _MIN_SEEDERS
    global _LOOKBACK_DAYS, _CHECK_HOUR, _DATA_DIR

    # TMDB
    _TMDB_API_KEY = (api.config.get("tmdb_api_key")
                     or os.environ.get("RELEASE_RADAR_TMDB_KEY", ""))

    # Jackett (share config with hunter plugin)
    _JACKETT_URL = (api.config.get("jackett_url")
                    or os.environ.get("HUNTER_JACKETT_URL", _JACKETT_URL))
    _JACKETT_API_KEY = (api.config.get("jackett_api_key")
                        or os.environ.get("HUNTER_JACKETT_API_KEY", _JACKETT_API_KEY))

    # qBittorrent (share config with hunter plugin)
    _QBIT_URL = (api.config.get("qbit_url")
                 or os.environ.get("HUNTER_QBIT_URL", _QBIT_URL))
    _QBIT_USER = (api.config.get("qbit_user")
                  or os.environ.get("HUNTER_QBIT_USER", _QBIT_USER))
    _QBIT_PASS = (api.config.get("qbit_pass")
                  or os.environ.get("HUNTER_QBIT_PASS", _QBIT_PASS))

    # Tuning
    _MIN_RATING = float(os.environ.get("RELEASE_RADAR_MIN_RATING", _MIN_RATING))
    _MIN_POPULARITY = float(os.environ.get("RELEASE_RADAR_MIN_POPULARITY", _MIN_POPULARITY))
    _MIN_SEEDERS = int(os.environ.get("RELEASE_RADAR_MIN_SEEDERS", _MIN_SEEDERS))
    _LOOKBACK_DAYS = int(os.environ.get("RELEASE_RADAR_LOOKBACK_DAYS", _LOOKBACK_DAYS))
    _CHECK_HOUR = int(os.environ.get("RELEASE_RADAR_CHECK_HOUR", _CHECK_HOUR))
    _DATA_DIR = os.environ.get("RELEASE_RADAR_DATA_DIR", _DATA_DIR)

    if not _TMDB_API_KEY:
        api.log("RELEASE_RADAR_TMDB_KEY not set — auto-check disabled. "
                "Get a free key at https://www.themoviedb.org/settings/api",
                level="warning")

    # -- release_radar_check --
    api.register_tool(
        name="release_radar_check",
        description=(
            "Check for new movies that recently hit digital release (rental/purchase) "
            "and are NOT on the user's streaming services (Disney+, Netflix, Hulu). "
            "Automatically downloads the best available torrent for each qualifying movie. "
            "Runs automatically once daily, but can be triggered manually."
        ),
        parameters={"type": "object", "properties": {}},
        handler=_release_radar_check,
        permission="none",
    )

    # -- release_radar_history --
    api.register_tool(
        name="release_radar_history",
        description=(
            "Show movies that were automatically grabbed by the release radar. "
            "Displays title, year, rating, and when it was grabbed."
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
