"""Hunter plugin — media automation via Sonarr, Radarr, Prowlarr + qBittorrent.

Tools:
- torrent_search: Search indexers via Prowlarr
- torrent_download: Send a magnet link to qBittorrent and record in history
- torrent_status: List active/completed downloads from qBittorrent
- torrent_history: Show downloads grabbed through the assistant
- media_vault_hunt: Process a batch of entries from the media vault
- media_vault_status: Show media vault collection progress
- media_add_movie: Add a movie to Radarr by title or TMDB ID
- media_add_series: Add a series to Sonarr by title or TVDB ID

Hooks:
- heartbeat_tick: Auto-hunt from the media vault every hour
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import httpx

from server.plugins import PluginAPI

log = logging.getLogger("conduit.plugin.hunter")

# ---------------------------------------------------------------------------
# Defaults (overridable via env vars)
# ---------------------------------------------------------------------------
_SONARR_URL = "http://localhost:8989"
_SONARR_API_KEY = ""
_RADARR_URL = "http://localhost:7878"
_RADARR_API_KEY = ""
_PROWLARR_URL = "http://localhost:9696"
_PROWLARR_API_KEY = ""
_QBIT_URL = "http://localhost:8080"
_QBIT_USER = "admin"
_QBIT_PASS = "adminadmin"
_MIN_SEEDERS = 5
_DATA_DIR = "~/.config/hunter"
_VAULT_JSON_PATH = "~/Documents/Work/media_vault.json"
_VAULT_BATCH_SIZE = 5
_VAULT_HUNT_INTERVAL = 3600  # seconds between auto-hunts
_VAULT_MAX_SIZE_TB = 5.0

# Prowlarr category codes (Newznab standard)
_PROWLARR_CATEGORIES = {
    "movies": [2000],
    "tv": [5000],
    "music": [3000],
    "books": [7000],
    "software": [4000],
}

# Normalize category names to qBit titlecase categories
_QBIT_CATEGORY_MAP = {
    "movies": "Movies", "movie": "Movies",
    "tv": "TV", "television": "TV", "tvshows": "TV",
    "documentaries": "Documentaries", "documentary": "Documentaries", "docs": "Documentaries",
    "kids": "Kids", "children": "Kids",
    "concerts": "Concerts", "concert": "Concerts",
    "stand-up": "Stand-Up", "standup": "Stand-Up", "comedy": "Stand-Up",
    "audio": "Audio", "music": "Audio",
}


def _normalize_category(cat: str) -> str:
    """Normalize a category string to the canonical qBit category name."""
    if not cat:
        return "Movies"
    if cat in ("Movies", "TV", "Documentaries", "Kids", "Concerts",
               "Stand-Up", "Audio"):
        return cat
    return _QBIT_CATEGORY_MAP.get(cat.lower(), cat)


# ---------------------------------------------------------------------------
# Vault category routing
# ---------------------------------------------------------------------------
# Determines where each vault category goes:
# "radarr" = movies/docs/standup, "sonarr" = tv series, "prowlarr" = direct search
_VAULT_ROUTING = {
    # category -> (service, root_folder, qbit_category)
    "Movies":         ("radarr", "/data/Movies", "Movies"),
    "Culinary":       ("radarr", "/data/Movies", "Movies"),
    "Criterion":      ("radarr", "/data/Movies", "Movies"),
    "Horror":         ("radarr", "/data/Movies", "Movies"),
    "Classics":       ("radarr", "/data/Movies", "Movies"),
    "Documentaries":  ("radarr", "/data/Documentaries", "Documentaries"),
    "Stand-Up":       ("radarr", "/data/Movies", "Movies"),
    "TV":             ("sonarr", "/data/TV", "TV"),
    "Anime":          ("sonarr", "/data/TV", "TV"),
    "Kids":           ("sonarr", "/data/Kids", "Kids"),  # default to series; overridden for movies
    "Concerts":       ("prowlarr", "/data/Concerts", "Concerts"),
    "Music":          ("prowlarr", "/data/Concerts", "Concerts"),
    "Audio":          ("prowlarr", "/data/Audio", "Audio"),
}

# Subcategory overrides (more specific)
_VAULT_SUBCAT_ROUTING = {
    "Feature Films":   ("radarr", "/data/Movies", "Movies"),
    "Documentaries":   ("radarr", "/data/Documentaries", "Documentaries"),
    "Music Docs":      ("radarr", "/data/Documentaries", "Documentaries"),
    "CC Roasts":       ("radarr", "/data/Movies", "Movies"),
    "Stand-Up":        ("radarr", "/data/Movies", "Movies"),
    "Buddy Comedy":    ("radarr", "/data/Movies", "Movies"),
    "Classics":        ("radarr", "/data/Movies", "Movies"),
    "Horror":          ("radarr", "/data/Movies", "Movies"),
    "Criterion":       ("radarr", "/data/Movies", "Movies"),
    "Prestige TV":     ("sonarr", "/data/TV", "TV"),
    "Anime":           ("sonarr", "/data/TV", "TV"),
    "Hip-Hop":         ("prowlarr", "/data/Audio", "Audio"),
}


def _get_vault_route(entry: dict) -> tuple[str, str, str]:
    """Return (service, root_folder, qbit_category) for a vault entry."""
    subcat = entry.get("subcategory", "")
    cat = entry.get("category", "")
    entry_type = entry.get("type", "movie")

    # Subcategory override first
    if subcat in _VAULT_SUBCAT_ROUTING:
        return _VAULT_SUBCAT_ROUTING[subcat]

    # Kids: movies go to Radarr, series go to Sonarr
    if cat == "Kids" and entry_type == "movie":
        return ("radarr", "/data/Kids", "Kids")

    # Category lookup
    if cat in _VAULT_ROUTING:
        return _VAULT_ROUTING[cat]

    # Fallback by type
    if entry_type == "series":
        return ("sonarr", "/data/TV", "TV")
    if entry_type == "music":
        return ("prowlarr", "/data/Audio", "Audio")
    return ("radarr", "/data/Movies", "Movies")


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_qbit_sid: str | None = None
_db: _HunterDB | None = None
_last_vault_hunt: float = 0


# ---------------------------------------------------------------------------
# SQLite history + vault tracking
# ---------------------------------------------------------------------------
class _HunterDB:
    def __init__(self, db_path: Path):
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                info_hash TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                source TEXT NOT NULL,
                size INTEGER NOT NULL,
                seeders INTEGER NOT NULL,
                grabbed_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'grabbed'
            )"""
        )
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS vault (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                year INTEGER,
                category TEXT NOT NULL,
                subcategory TEXT,
                type TEXT NOT NULL,
                notes TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                search_query TEXT,
                result_title TEXT,
                magnet TEXT,
                info_hash TEXT,
                size_bytes INTEGER DEFAULT 0,
                seeders INTEGER DEFAULT 0,
                last_searched TEXT,
                downloaded_at TEXT,
                retry_count INTEGER DEFAULT 0,
                arr_id INTEGER,
                UNIQUE(title, year)
            )"""
        )
        # Add arr_id column if upgrading from old schema
        try:
            self._conn.execute("ALTER TABLE vault ADD COLUMN arr_id INTEGER")
        except sqlite3.OperationalError:
            pass  # column already exists
        self._conn.commit()

    # -- history methods --
    def add(self, info_hash: str, name: str, category: str, source: str,
            size: int, seeders: int) -> bool:
        try:
            self._conn.execute(
                """INSERT INTO history
                   (info_hash, name, category, source, size, seeders, grabbed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (info_hash.lower(), name, category, source, size, seeders,
                 datetime.now(timezone.utc).isoformat()),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def has_hash(self, info_hash: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM history WHERE info_hash = ?",
            (info_hash.lower(),),
        ).fetchone()
        return row is not None

    def list_recent(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM history ORDER BY grabbed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- vault methods --
    def vault_sync(self, entries: list[dict]) -> int:
        added = 0
        for e in entries:
            try:
                self._conn.execute(
                    """INSERT OR IGNORE INTO vault
                       (title, year, category, subcategory, type, notes)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (e["title"], e.get("year"), e["category"],
                     e.get("subcategory", ""), e["type"], e.get("notes", "")),
                )
                added += self._conn.execute(
                    "SELECT changes()").fetchone()[0]
            except sqlite3.Error:
                continue
        self._conn.commit()
        return added

    def vault_pending(self, limit: int = 5) -> list[dict]:
        rows = self._conn.execute(
            """SELECT * FROM vault
               WHERE status IN ('pending', 'retry')
               ORDER BY id ASC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def vault_update(self, vault_id: int, **kwargs) -> None:
        sets = []
        vals = []
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        vals.append(vault_id)
        self._conn.execute(
            f"UPDATE vault SET {', '.join(sets)} WHERE id = ?", vals)
        self._conn.commit()

    def vault_stats(self) -> dict:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) as cnt FROM vault GROUP BY status"
        ).fetchall()
        stats = {r["status"]: r["cnt"] for r in rows}
        total = sum(stats.values())
        total_size = self._conn.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) FROM vault WHERE status = 'downloaded'"
        ).fetchone()[0]
        return {"total": total, "by_status": stats, "total_size_bytes": total_size}

    def vault_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM vault").fetchone()[0]


def _get_db() -> _HunterDB:
    global _db
    if _db is None:
        data_dir = Path(_DATA_DIR).expanduser()
        data_dir.mkdir(parents=True, exist_ok=True)
        _db = _HunterDB(data_dir / "hunter.db")
    return _db


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


async def _radarr_lookup(term: str) -> list[dict]:
    """Search TMDB via Radarr for a movie."""
    return await _radarr_get("/movie/lookup", {"term": term})


async def _radarr_add_movie(
    tmdb_id: int,
    title: str,
    year: int | None,
    root_folder: str,
    search: bool = True,
) -> dict:
    """Add a movie to Radarr. Returns the created movie dict."""
    # Get quality profile (prefer HD-1080p)
    profiles = await _radarr_get("/qualityprofile")
    profile_id = next(
        (p["id"] for p in profiles if p["name"] == "HD-1080p"),
        profiles[0]["id"] if profiles else 1,
    )

    payload = {
        "tmdbId": tmdb_id,
        "title": title,
        "year": year or 0,
        "rootFolderPath": root_folder,
        "qualityProfileId": profile_id,
        "monitored": True,
        "addOptions": {
            "searchForMovie": search,
        },
    }
    return await _radarr_post("/movie", payload)


async def _radarr_get_movie_by_tmdb(tmdb_id: int) -> dict | None:
    """Check if a movie is already in Radarr."""
    movies = await _radarr_get("/movie")
    for m in movies:
        if m.get("tmdbId") == tmdb_id:
            return m
    return None


async def _radarr_put(endpoint: str, data: dict) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.put(
            f"{_RADARR_URL}/api/v3{endpoint}",
            json=data,
            headers={"X-Api-Key": _RADARR_API_KEY},
        )
        resp.raise_for_status()
        return resp.json()


async def _radarr_ensure_tag(label: str) -> int:
    """Get or create a Radarr tag by label. Returns the tag ID."""
    tags = await _radarr_get("/tag")
    for t in tags:
        if t["label"].lower() == label.lower():
            return t["id"]
    new_tag = await _radarr_post("/tag", {"label": label.lower()})
    return new_tag["id"]


async def _radarr_apply_tag(movie_id: int, tag_id: int):
    """Add a tag to a Radarr movie if not already present."""
    movie = await _radarr_get(f"/movie/{movie_id}")
    tags = movie.get("tags", [])
    if tag_id not in tags:
        movie["tags"] = tags + [tag_id]
        await _radarr_put(f"/movie/{movie_id}", movie)


# ---------------------------------------------------------------------------
# Sonarr API
# ---------------------------------------------------------------------------
async def _sonarr_get(endpoint: str, params: dict | None = None) -> dict | list:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{_SONARR_URL}/api/v3{endpoint}",
            params=params,
            headers={"X-Api-Key": _SONARR_API_KEY},
        )
        resp.raise_for_status()
        return resp.json()


async def _sonarr_post(endpoint: str, data: dict) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{_SONARR_URL}/api/v3{endpoint}",
            json=data,
            headers={"X-Api-Key": _SONARR_API_KEY},
        )
        resp.raise_for_status()
        return resp.json()


async def _sonarr_lookup(term: str) -> list[dict]:
    """Search TVDB via Sonarr for a series."""
    return await _sonarr_get("/series/lookup", {"term": term})


async def _sonarr_add_series(
    tvdb_id: int,
    title: str,
    root_folder: str,
    search: bool = True,
) -> dict:
    """Add a series to Sonarr. Returns the created series dict."""
    profiles = await _sonarr_get("/qualityprofile")
    profile_id = next(
        (p["id"] for p in profiles if p["name"] == "HD-1080p"),
        profiles[0]["id"] if profiles else 1,
    )

    # Get full series data from lookup for seasons/images
    lookup = await _sonarr_get("/series/lookup", {"term": f"tvdb:{tvdb_id}"})
    series_data = lookup[0] if lookup else {}

    payload = {
        "tvdbId": tvdb_id,
        "title": title,
        "rootFolderPath": root_folder,
        "qualityProfileId": profile_id,
        "monitored": True,
        "seasonFolder": True,
        "seasons": series_data.get("seasons", []),
        "addOptions": {
            "searchForMissingEpisodes": search,
        },
    }
    return await _sonarr_post("/series", payload)


async def _sonarr_get_series_by_tvdb(tvdb_id: int) -> dict | None:
    """Check if a series is already in Sonarr."""
    series = await _sonarr_get("/series")
    for s in series:
        if s.get("tvdbId") == tvdb_id:
            return s
    return None


# ---------------------------------------------------------------------------
# Prowlarr API (for direct search — concerts, audio, etc.)
# ---------------------------------------------------------------------------
async def _prowlarr_search(query: str, categories: list[int] | None = None) -> list[dict]:
    """Search indexers via Prowlarr. Returns list of results."""
    params: dict = {"query": query, "type": "search"}
    if categories:
        params["categories"] = categories

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{_PROWLARR_URL}/api/v1/search",
            params=params,
            headers={"X-Api-Key": _PROWLARR_API_KEY},
        )
        resp.raise_for_status()
        results = resp.json()

    # Normalize to a common format
    normalized = []
    for r in results:
        magnet = r.get("magnetUrl") or r.get("downloadUrl", "")
        info_hash = r.get("infoHash")
        if not info_hash and magnet:
            m = re.search(r"btih:([a-fA-F0-9]{40})", magnet)
            if m:
                info_hash = m.group(1)

        normalized.append({
            "title": r.get("title", ""),
            "magnet": magnet if magnet.startswith("magnet:") else "",
            "link": r.get("downloadUrl", ""),
            "guid": r.get("guid", ""),
            "size": r.get("size", 0),
            "seeders": r.get("seeders", 0),
            "peers": r.get("leechers", 0),
            "info_hash": info_hash,
            "source": r.get("indexer", "unknown"),
            "categories": r.get("categories", []),
        })

    # Filter by min seeders and sort by seeders desc
    normalized = [r for r in normalized if r["seeders"] >= _MIN_SEEDERS]
    normalized.sort(key=lambda r: r["seeders"], reverse=True)
    return normalized


# ---------------------------------------------------------------------------
# qBittorrent API
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


async def _qbit_request(method: str, endpoint: str, **kwargs) -> httpx.Response:
    global _qbit_sid
    url = f"{_QBIT_URL}{endpoint}"
    headers = {"Referer": _QBIT_URL}

    async with httpx.AsyncClient(timeout=15) as client:
        if _qbit_sid:
            client.cookies.set("SID", _qbit_sid)
        resp = await client.request(method, url, headers=headers, **kwargs)
        if resp.status_code == 403:
            sid = await _qbit_login(client)
            if not sid:
                resp.raise_for_status()
            client.cookies.set("SID", sid)
            resp = await client.request(method, url, headers=headers, **kwargs)
        resp.raise_for_status()
        return resp


async def _qbit_add_torrent(magnet: str, category: str = "") -> bool:
    data = {"urls": magnet}
    if category:
        data["category"] = category
    resp = await _qbit_request("POST", "/api/v2/torrents/add", data=data)
    return resp.status_code == 200


async def _qbit_list_torrents(filter_: str = "all") -> list[dict]:
    resp = await _qbit_request("GET", "/api/v2/torrents/info",
                               params={"filter": filter_})
    return resp.json()


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def _human_size(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(nbytes) < 1024:
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024  # type: ignore[assignment]
    return f"{nbytes:.1f} PB"


def _format_speed(bps: int) -> str:
    if bps <= 0:
        return "0 B/s"
    return f"{_human_size(bps)}/s"


def _format_progress(progress: float) -> str:
    return f"{progress * 100:.1f}%"


# ---------------------------------------------------------------------------
# Media Vault hunting logic (rewritten for arr stack)
# ---------------------------------------------------------------------------
def _build_search_query(entry: dict) -> str:
    title = entry["title"]
    year = entry.get("year")
    if year and entry.get("type") == "movie":
        return f"{title} {year}"
    return title


def _score_prowlarr_result(result: dict, entry: dict) -> float:
    """Score a Prowlarr search result for quality/relevance. Higher is better."""
    title_lower = result["title"].lower()
    score = result["seeders"]

    if "1080p" in title_lower:
        score *= 3
    elif "2160p" in title_lower or "4k" in title_lower:
        score *= 1.5
    elif "720p" in title_lower:
        score *= 2
    elif "480p" in title_lower or "dvdrip" in title_lower:
        score *= 0.5

    size_gb = result["size"] / (1024 ** 3) if result["size"] else 0
    if size_gb > 50:
        score *= 0.1
    elif size_gb > 20:
        score *= 0.5

    if "bluray" in title_lower or "web-dl" in title_lower:
        score *= 1.2
    if "webrip" in title_lower or "web" in title_lower:
        score *= 1.1

    if any(t in title_lower for t in ("cam", "hdts", "screener", "telesync")):
        score *= 0.01

    return score


async def _hunt_entry_radarr(entry: dict, root_folder: str) -> dict:
    """Add a movie to Radarr and let it handle the download."""
    query = _build_search_query(entry)

    try:
        results = await _radarr_lookup(query)
    except httpx.ConnectError:
        return {"status": "retry", "message": "Radarr unreachable"}
    except Exception as e:
        return {"status": "retry", "message": f"Radarr lookup error: {e}"}

    if not results:
        return {"status": "not_found", "message": "No TMDB match found via Radarr"}

    # Pick the best match — prefer exact year match
    best = results[0]
    if entry.get("year"):
        for r in results:
            if r.get("year") == entry["year"]:
                best = r
                break

    tmdb_id = best.get("tmdbId")
    if not tmdb_id:
        return {"status": "not_found", "message": "No TMDB ID in lookup result"}

    # Check if already in Radarr
    try:
        existing = await _radarr_get_movie_by_tmdb(tmdb_id)
        if existing:
            # Still apply vault category tag even if movie already exists
            existing_id = existing.get("id")
            category = entry.get("category", "")
            if existing_id and category:
                try:
                    tag_id = await _radarr_ensure_tag(category)
                    await _radarr_apply_tag(existing_id, tag_id)
                except Exception:
                    pass
            return {
                "status": "downloaded",
                "message": f"Already in Radarr: {best.get('title', entry['title'])}",
                "arr_id": existing_id,
            }
    except Exception:
        pass  # continue to add

    # Add to Radarr with search
    try:
        movie = await _radarr_add_movie(
            tmdb_id=tmdb_id,
            title=best.get("title", entry["title"]),
            year=best.get("year", entry.get("year")),
            root_folder=root_folder,
            search=True,
        )
    except httpx.HTTPStatusError as e:
        body = e.response.text[:200] if e.response else ""
        if "already been added" in body.lower() or e.response.status_code == 400:
            return {
                "status": "downloaded",
                "message": f"Already in Radarr: {best.get('title', entry['title'])}",
            }
        return {"status": "retry", "message": f"Radarr add failed: {e}"}
    except Exception as e:
        return {"status": "retry", "message": f"Radarr add error: {e}"}

    # Apply vault category as a Radarr tag for collection sync discovery
    movie_id = movie.get("id")
    category = entry.get("category", "")
    if movie_id and category:
        try:
            tag_id = await _radarr_ensure_tag(category)
            await _radarr_apply_tag(movie_id, tag_id)
        except Exception as e:
            log.warning("Failed to apply tag '%s' to movie %s: %s", category, movie_id, e)

    return {
        "status": "downloaded",
        "message": f"Added to Radarr: {movie.get('title', entry['title'])} ({movie.get('year', '?')})",
        "arr_id": movie_id,
        "result_title": movie.get("title", ""),
    }


async def _hunt_entry_sonarr(entry: dict, root_folder: str) -> dict:
    """Add a series to Sonarr and let it handle the download."""
    query = entry["title"]

    try:
        results = await _sonarr_lookup(query)
    except httpx.ConnectError:
        return {"status": "retry", "message": "Sonarr unreachable"}
    except Exception as e:
        return {"status": "retry", "message": f"Sonarr lookup error: {e}"}

    if not results:
        return {"status": "not_found", "message": "No TVDB match found via Sonarr"}

    best = results[0]
    tvdb_id = best.get("tvdbId")
    if not tvdb_id:
        return {"status": "not_found", "message": "No TVDB ID in lookup result"}

    # Check if already in Sonarr
    try:
        existing = await _sonarr_get_series_by_tvdb(tvdb_id)
        if existing:
            return {
                "status": "downloaded",
                "message": f"Already in Sonarr: {best.get('title', entry['title'])}",
                "arr_id": existing.get("id"),
            }
    except Exception:
        pass

    # Add to Sonarr with search
    try:
        series = await _sonarr_add_series(
            tvdb_id=tvdb_id,
            title=best.get("title", entry["title"]),
            root_folder=root_folder,
            search=True,
        )
    except httpx.HTTPStatusError as e:
        body = e.response.text[:200] if e.response else ""
        if "already been added" in body.lower() or e.response.status_code == 400:
            return {
                "status": "downloaded",
                "message": f"Already in Sonarr: {best.get('title', entry['title'])}",
            }
        return {"status": "retry", "message": f"Sonarr add failed: {e}"}
    except Exception as e:
        return {"status": "retry", "message": f"Sonarr add error: {e}"}

    return {
        "status": "downloaded",
        "message": f"Added to Sonarr: {series.get('title', entry['title'])}",
        "arr_id": series.get("id"),
        "result_title": series.get("title", ""),
    }


async def _hunt_entry_prowlarr(entry: dict, qbit_category: str) -> dict:
    """Direct search via Prowlarr + download via qBit (concerts, audio, etc.)."""
    query = _build_search_query(entry)
    entry_type = entry.get("type", "movie")

    # Map to Prowlarr categories
    cats = None
    if entry_type in ("music", "audio"):
        cats = [3000]
    elif entry_type == "movie":
        cats = [2000]

    try:
        results = await _prowlarr_search(query, cats)
    except httpx.ConnectError:
        return {"status": "retry", "message": "Prowlarr unreachable"}
    except Exception as e:
        return {"status": "retry", "message": f"Prowlarr search error: {e}"}

    if not results:
        # Retry without year
        if entry.get("year") and entry.get("type") == "movie":
            try:
                results = await _prowlarr_search(entry["title"], cats)
            except Exception:
                pass

    if not results:
        return {"status": "not_found", "message": "No results with enough seeders"}

    # Score and pick the best
    for r in results:
        r["_score"] = _score_prowlarr_result(r, entry)
    results.sort(key=lambda r: r["_score"], reverse=True)

    best = results[0]
    magnet = best.get("magnet")
    if not magnet:
        return {"status": "not_found", "message": "Best result has no magnet link"}

    # Download via qBit
    try:
        ok = await _qbit_add_torrent(magnet, qbit_category)
    except httpx.ConnectError:
        return {"status": "retry", "message": "qBittorrent unreachable"}
    except Exception as e:
        return {"status": "retry", "message": f"Download error: {e}"}

    if not ok:
        return {"status": "retry", "message": "qBittorrent rejected torrent"}

    # Record in history
    info_hash = best.get("info_hash")
    if info_hash:
        db = _get_db()
        db.add(
            info_hash=info_hash,
            name=entry["title"],
            category=qbit_category,
            source="vault",
            size=best.get("size", 0),
            seeders=best.get("seeders", 0),
        )

    return {
        "status": "downloaded",
        "message": f"Grabbed via Prowlarr: {best['title']}",
        "result_title": best["title"],
        "magnet": magnet,
        "info_hash": info_hash or "",
        "size_bytes": best.get("size", 0),
        "seeders": best.get("seeders", 0),
    }


async def _hunt_entry(entry: dict) -> dict:
    """Route a vault entry to the appropriate service and hunt it."""
    service, root_folder, qbit_cat = _get_vault_route(entry)

    if service == "radarr":
        if not _RADARR_API_KEY:
            return {"status": "retry", "message": "Radarr API key not configured"}
        return await _hunt_entry_radarr(entry, root_folder)
    elif service == "sonarr":
        if not _SONARR_API_KEY:
            return {"status": "retry", "message": "Sonarr API key not configured"}
        return await _hunt_entry_sonarr(entry, root_folder)
    else:  # prowlarr
        if not _PROWLARR_API_KEY:
            return {"status": "retry", "message": "Prowlarr API key not configured"}
        return await _hunt_entry_prowlarr(entry, qbit_cat)


async def _hunt_batch(batch_size: int | None = None) -> str:
    """Process a batch of pending vault entries. Returns status report."""
    size = batch_size or _VAULT_BATCH_SIZE
    db = _get_db()

    # Ensure vault is loaded
    vault_path = Path(_VAULT_JSON_PATH).expanduser()
    if vault_path.exists() and db.vault_count() == 0:
        with open(vault_path) as f:
            data = json.load(f)
        added = db.vault_sync(data.get("entries", []))
        log.info("Media Vault: synced %d new entries from JSON", added)

    # Check disk budget
    stats = db.vault_stats()
    total_tb = stats["total_size_bytes"] / (1024 ** 4)
    if total_tb >= _VAULT_MAX_SIZE_TB:
        return (f"Media Vault: disk budget reached "
                f"({_human_size(stats['total_size_bytes'])} / "
                f"{_VAULT_MAX_SIZE_TB} TB). Pausing hunts.")

    pending = db.vault_pending(size)
    if not pending:
        return "Media Vault: no pending entries to hunt."

    results = []
    for entry in pending:
        db.vault_update(entry["id"], status="searching",
                        search_query=_build_search_query(entry),
                        last_searched=datetime.now(timezone.utc).isoformat())

        result = await _hunt_entry(entry)
        status = result["status"]

        update: dict = {"status": status}
        if status == "downloaded":
            update["result_title"] = result.get("result_title", "")
            update["downloaded_at"] = datetime.now(timezone.utc).isoformat()
            if result.get("arr_id"):
                update["arr_id"] = result["arr_id"]
            if result.get("magnet"):
                update["magnet"] = result["magnet"]
            if result.get("info_hash"):
                update["info_hash"] = result["info_hash"]
            if result.get("size_bytes"):
                update["size_bytes"] = result["size_bytes"]
            if result.get("seeders"):
                update["seeders"] = result["seeders"]
        elif status == "retry":
            update["retry_count"] = entry.get("retry_count", 0) + 1
            if update["retry_count"] >= 3:
                update["status"] = "not_found"

        db.vault_update(entry["id"], **update)
        results.append((entry["title"], result["status"], result["message"]))

    # Format report
    lines = [f"Media Vault: hunted {len(results)} entries\n"]
    for title, status, msg in results:
        icon = {"downloaded": "+", "not_found": "x",
                "retry": "~"}.get(status, "?")
        lines.append(f"  [{icon}] {title} — {msg}")

    stats = db.vault_stats()
    by_s = stats["by_status"]
    lines.append(f"\nProgress: {by_s.get('downloaded', 0)} downloaded, "
                 f"{by_s.get('not_found', 0)} not found, "
                 f"{by_s.get('pending', 0) + by_s.get('retry', 0)} remaining "
                 f"/ {stats['total']} total")
    lines.append(f"Disk: {_human_size(stats['total_size_bytes'])} / "
                 f"{_VAULT_MAX_SIZE_TB} TB")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------
async def _torrent_search(query: str, category: str | None = None) -> str:
    """Search indexers via Prowlarr."""
    if not _PROWLARR_API_KEY:
        return "Error: HUNTER_PROWLARR_API_KEY not configured."

    cats = None
    if category and category.lower() in _PROWLARR_CATEGORIES:
        cats = _PROWLARR_CATEGORIES[category.lower()]

    try:
        results = await _prowlarr_search(query, cats)
    except httpx.HTTPStatusError as e:
        return f"Error: Prowlarr returned {e.response.status_code}."
    except httpx.ConnectError:
        return "Error: Cannot connect to Prowlarr. Is it running?"
    except Exception as e:
        return f"Error searching Prowlarr: {e}"

    if not results:
        return (f"No results found for \"{query}\" with at least "
                f"{_MIN_SEEDERS} seeders.")

    results = results[:15]
    lines = [f"Found {len(results)} results for \"{query}\":\n"]
    for i, r in enumerate(results, 1):
        size = _human_size(r["size"]) if r["size"] else "unknown"
        line = f"{i}. {r['title']} | {size} | {r['seeders']} seeds | {r['source']}"
        if r.get("magnet"):
            line += f"\n   {r['magnet']}"
        elif r.get("link"):
            line += f"\n   {r['link']}"
        lines.append(line)

    return "\n".join(lines)


async def _torrent_download(magnet: str, category: str = "",
                            name: str = "") -> str:
    if not magnet.startswith("magnet:"):
        return "Error: Invalid magnet link — must start with 'magnet:'."

    category = _normalize_category(category)

    try:
        ok = await _qbit_add_torrent(magnet, category)
    except httpx.ConnectError:
        return "Error: Cannot connect to qBittorrent. Is it running?"
    except httpx.HTTPStatusError as e:
        return f"Error: qBittorrent returned {e.response.status_code}."
    except Exception as e:
        return f"Error adding torrent: {e}"

    if not ok:
        return "Error: qBittorrent rejected the torrent."

    info_hash = None
    m = re.search(r"btih:([a-fA-F0-9]{40})", magnet)
    if m:
        info_hash = m.group(1)

    display_name = name or "unknown"
    if info_hash:
        db = _get_db()
        if db.has_hash(info_hash):
            return (f"Sent to qBittorrent: {display_name} "
                    f"(already in history, possible re-download).")
        db.add(info_hash=info_hash, name=display_name,
               category=category or "uncategorized",
               source="conduit", size=0, seeders=0)

    cat_note = f" [{category}]" if category else ""
    return f"Sent to qBittorrent: {display_name}{cat_note}"


async def _torrent_status(filter: str = "all") -> str:
    valid_filters = ("all", "downloading", "seeding", "completed", "paused",
                     "active", "inactive", "stalled", "errored")
    if filter not in valid_filters:
        return f"Invalid filter. Use one of: {', '.join(valid_filters)}"

    try:
        torrents = await _qbit_list_torrents(filter)
    except httpx.ConnectError:
        return "Error: Cannot connect to qBittorrent. Is it running?"
    except httpx.HTTPStatusError as e:
        return f"Error: qBittorrent returned {e.response.status_code}."
    except Exception as e:
        return f"Error listing torrents: {e}"

    if not torrents:
        return f"No torrents ({filter})."

    lines = [f"{len(torrents)} torrents ({filter}):\n"]
    for t in torrents[:25]:
        name = t.get("name", "?")
        state = t.get("state", "?")
        progress = _format_progress(t.get("progress", 0))
        size = _human_size(t.get("size", 0))
        dlspeed = _format_speed(t.get("dlspeed", 0))
        cat = t.get("category", "")

        line = f"- {name} | {state} | {progress} | {size}"
        if t.get("dlspeed", 0) > 0:
            line += f" | {dlspeed}"
        if cat:
            line += f" | [{cat}]"
        lines.append(line)

    if len(torrents) > 25:
        lines.append(f"\n... and {len(torrents) - 25} more")

    return "\n".join(lines)


async def _torrent_history(limit: int = 20) -> str:
    db = _get_db()
    entries = db.list_recent(limit)

    if not entries:
        return "No download history yet."

    lines = [f"Last {len(entries)} downloads:\n"]
    for e in entries:
        size = _human_size(e["size"]) if e["size"] else "n/a"
        grabbed = e["grabbed_at"][:19].replace("T", " ")
        line = f"- {e['name']} | {e['category']} | {size} | {grabbed}"
        lines.append(line)

    return "\n".join(lines)


async def _media_vault_hunt(batch_size: int = 5) -> str:
    return await _hunt_batch(batch_size)


async def _media_vault_status() -> str:
    db = _get_db()

    # Sync vault if empty
    vault_path = Path(_VAULT_JSON_PATH).expanduser()
    if vault_path.exists() and db.vault_count() == 0:
        with open(vault_path) as f:
            data = json.load(f)
        db.vault_sync(data.get("entries", []))

    if db.vault_count() == 0:
        return "Media Vault: no entries loaded. Check MEDIA_VAULT_JSON_PATH."

    stats = db.vault_stats()
    by_s = stats["by_status"]
    total = stats["total"]
    downloaded = by_s.get("downloaded", 0)
    not_found = by_s.get("not_found", 0)
    pending = by_s.get("pending", 0) + by_s.get("retry", 0)
    searching = by_s.get("searching", 0)
    pct = (downloaded / total * 100) if total else 0

    lines = [
        f"Media Vault Collection Status",
        f"{'=' * 35}",
        f"Total entries:  {total}",
        f"Downloaded:     {downloaded} ({pct:.1f}%)",
        f"Not found:      {not_found}",
        f"Pending:        {pending}",
        f"In progress:    {searching}",
        f"",
        f"Disk usage:     {_human_size(stats['total_size_bytes'])} / "
        f"{_VAULT_MAX_SIZE_TB} TB",
    ]

    rows = db._conn.execute(
        """SELECT category, status, COUNT(*) as cnt
           FROM vault GROUP BY category, status
           ORDER BY category"""
    ).fetchall()
    if rows:
        cat_stats: dict[str, dict] = {}
        for r in rows:
            cat = r["category"]
            if cat not in cat_stats:
                cat_stats[cat] = {}
            cat_stats[cat][r["status"]] = r["cnt"]

        lines.append(f"\nBy category:")
        for cat in sorted(cat_stats):
            cs = cat_stats[cat]
            cat_total = sum(cs.values())
            cat_dl = cs.get("downloaded", 0)
            lines.append(f"  {cat}: {cat_dl}/{cat_total}")

    return "\n".join(lines)


async def _media_add_movie(title: str, tmdb_id: int | None = None,
                           root_folder: str = "/data/Movies") -> str:
    """Add a movie to Radarr by title or TMDB ID."""
    if not _RADARR_API_KEY:
        return "Error: HUNTER_RADARR_API_KEY not configured."

    try:
        if tmdb_id:
            # Check if already in Radarr
            existing = await _radarr_get_movie_by_tmdb(tmdb_id)
            if existing:
                return f"Already in Radarr: {existing.get('title')} ({existing.get('year')})"
            # Look up to get full data
            results = await _radarr_lookup(f"tmdb:{tmdb_id}")
        else:
            results = await _radarr_lookup(title)
    except httpx.ConnectError:
        return "Error: Cannot connect to Radarr."
    except Exception as e:
        return f"Error looking up movie: {e}"

    if not results:
        return f"No TMDB match found for \"{title}\"."

    best = results[0]
    tid = tmdb_id or best.get("tmdbId")
    if not tid:
        return "Error: No TMDB ID found in results."

    try:
        movie = await _radarr_add_movie(
            tmdb_id=tid,
            title=best.get("title", title),
            year=best.get("year"),
            root_folder=root_folder,
            search=True,
        )
    except httpx.HTTPStatusError as e:
        body = e.response.text[:200] if e.response else ""
        if "already been added" in body.lower():
            return f"Already in Radarr: {best.get('title', title)}"
        return f"Radarr error: {e}"
    except Exception as e:
        return f"Error adding movie: {e}"

    return (f"Added to Radarr: {movie.get('title', title)} "
            f"({movie.get('year', '?')}) — searching for downloads...")


async def _media_add_series(title: str, tvdb_id: int | None = None,
                            root_folder: str = "/data/TV") -> str:
    """Add a series to Sonarr by title or TVDB ID."""
    if not _SONARR_API_KEY:
        return "Error: HUNTER_SONARR_API_KEY not configured."

    try:
        if tvdb_id:
            existing = await _sonarr_get_series_by_tvdb(tvdb_id)
            if existing:
                return f"Already in Sonarr: {existing.get('title')}"
            results = await _sonarr_lookup(f"tvdb:{tvdb_id}")
        else:
            results = await _sonarr_lookup(title)
    except httpx.ConnectError:
        return "Error: Cannot connect to Sonarr."
    except Exception as e:
        return f"Error looking up series: {e}"

    if not results:
        return f"No TVDB match found for \"{title}\"."

    best = results[0]
    tid = tvdb_id or best.get("tvdbId")
    if not tid:
        return "Error: No TVDB ID found in results."

    try:
        series = await _sonarr_add_series(
            tvdb_id=tid,
            title=best.get("title", title),
            root_folder=root_folder,
            search=True,
        )
    except httpx.HTTPStatusError as e:
        body = e.response.text[:200] if e.response else ""
        if "already been added" in body.lower():
            return f"Already in Sonarr: {best.get('title', title)}"
        return f"Sonarr error: {e}"
    except Exception as e:
        return f"Error adding series: {e}"

    return (f"Added to Sonarr: {series.get('title', title)} "
            f"— searching for missing episodes...")


# ---------------------------------------------------------------------------
# Heartbeat hook — auto-hunt every hour
# ---------------------------------------------------------------------------
async def _vault_hunt_heartbeat(**kwargs) -> dict | None:
    global _last_vault_hunt

    # Need at least one arr service configured
    if not (_RADARR_API_KEY or _SONARR_API_KEY or _PROWLARR_API_KEY):
        return None

    now = datetime.now(timezone.utc).timestamp()
    if now - _last_vault_hunt < _VAULT_HUNT_INTERVAL:
        return None

    _last_vault_hunt = now

    vault_path = Path(_VAULT_JSON_PATH).expanduser()
    if not vault_path.exists():
        return None

    log.info("Media Vault: auto-hunt triggered")

    try:
        report = await _hunt_batch()
        log.info("Media Vault: %s",
                 report.split("\n")[0] if report else "no report")

        if "[+]" in report:
            try:
                from server import ntfy
                await ntfy.push(
                    title="Media Vault Hunt",
                    body=report,
                    tags=["movie_camera", "robot"],
                )
            except Exception:
                pass
    except Exception as e:
        log.error("Media Vault auto-hunt failed: %s", e)

    return None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def register(api: PluginAPI):
    global _SONARR_URL, _SONARR_API_KEY, _RADARR_URL, _RADARR_API_KEY
    global _PROWLARR_URL, _PROWLARR_API_KEY
    global _QBIT_URL, _QBIT_USER, _QBIT_PASS
    global _MIN_SEEDERS, _DATA_DIR, _VAULT_JSON_PATH, _VAULT_BATCH_SIZE
    global _VAULT_HUNT_INTERVAL, _VAULT_MAX_SIZE_TB

    _SONARR_URL = (api.config.get("sonarr_url")
                   or os.environ.get("HUNTER_SONARR_URL", _SONARR_URL))
    _SONARR_API_KEY = (api.config.get("sonarr_api_key")
                       or os.environ.get("HUNTER_SONARR_API_KEY", _SONARR_API_KEY))
    _RADARR_URL = (api.config.get("radarr_url")
                   or os.environ.get("HUNTER_RADARR_URL", _RADARR_URL))
    _RADARR_API_KEY = (api.config.get("radarr_api_key")
                       or os.environ.get("HUNTER_RADARR_API_KEY", _RADARR_API_KEY))
    _PROWLARR_URL = (api.config.get("prowlarr_url")
                     or os.environ.get("HUNTER_PROWLARR_URL", _PROWLARR_URL))
    _PROWLARR_API_KEY = (api.config.get("prowlarr_api_key")
                         or os.environ.get("HUNTER_PROWLARR_API_KEY", _PROWLARR_API_KEY))
    _QBIT_URL = (api.config.get("qbit_url")
                 or os.environ.get("HUNTER_QBIT_URL", _QBIT_URL))
    _QBIT_USER = (api.config.get("qbit_user")
                  or os.environ.get("HUNTER_QBIT_USER", _QBIT_USER))
    _QBIT_PASS = (api.config.get("qbit_pass")
                  or os.environ.get("HUNTER_QBIT_PASS", _QBIT_PASS))
    _MIN_SEEDERS = int(api.config.get("min_seeders")
                       or os.environ.get("HUNTER_MIN_SEEDERS", _MIN_SEEDERS))
    _DATA_DIR = (api.config.get("data_dir")
                 or os.environ.get("HUNTER_DATA_DIR", _DATA_DIR))
    _VAULT_JSON_PATH = (api.config.get("vault_json_path")
                        or os.environ.get("MEDIA_VAULT_JSON_PATH",
                                          _VAULT_JSON_PATH))
    _VAULT_BATCH_SIZE = int(
        os.environ.get("MEDIA_VAULT_BATCH_SIZE", _VAULT_BATCH_SIZE))
    _VAULT_HUNT_INTERVAL = int(
        os.environ.get("MEDIA_VAULT_HUNT_INTERVAL", _VAULT_HUNT_INTERVAL))
    _VAULT_MAX_SIZE_TB = float(
        os.environ.get("MEDIA_VAULT_MAX_SIZE_TB", _VAULT_MAX_SIZE_TB))

    missing = []
    if not _RADARR_API_KEY:
        missing.append("HUNTER_RADARR_API_KEY")
    if not _SONARR_API_KEY:
        missing.append("HUNTER_SONARR_API_KEY")
    if not _PROWLARR_API_KEY:
        missing.append("HUNTER_PROWLARR_API_KEY")
    if missing:
        api.log(f"{', '.join(missing)} not set — some features disabled",
                level="warning")

    # -- torrent_search --
    api.register_tool(
        name="torrent_search",
        description=(
            "Search for torrents across indexers via Prowlarr. Returns ranked "
            "results with title, size, seeders, source, and magnet links. "
            "Optional category filter: movies, tv, music, books, software."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (e.g. 'The Matrix 1999').",
                },
                "category": {
                    "type": "string",
                    "description": "Optional: movies, tv, music, books, software.",
                },
            },
            "required": ["query"],
        },
        handler=_torrent_search,
        permission="none",
    )

    # -- torrent_download --
    api.register_tool(
        name="torrent_download",
        description=(
            "Send a magnet link to qBittorrent for downloading. "
            "Provide the full magnet URI from a torrent_search result."
        ),
        parameters={
            "type": "object",
            "properties": {
                "magnet": {
                    "type": "string",
                    "description": "Full magnet link (magnet:?xt=urn:btih:...).",
                },
                "category": {
                    "type": "string",
                    "description": "Download category: Movies, TV, Documentaries, Kids, Concerts, Stand-Up, Audio.",
                },
                "name": {
                    "type": "string",
                    "description": "Human-readable name for history.",
                },
            },
            "required": ["magnet"],
        },
        handler=_torrent_download,
        permission="none",
    )

    # -- torrent_status --
    api.register_tool(
        name="torrent_status",
        description=(
            "List torrents in qBittorrent with status, progress, speed, size."
        ),
        parameters={
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "description": "Filter: all, downloading, seeding, "
                                   "completed, paused, active, etc.",
                    "default": "all",
                },
            },
        },
        handler=_torrent_status,
        permission="none",
    )

    # -- torrent_history --
    api.register_tool(
        name="torrent_history",
        description="Show recent downloads grabbed through the assistant.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max entries to show (default: 20).",
                    "default": 20,
                },
            },
        },
        handler=_torrent_history,
        permission="none",
    )

    # -- media_vault_hunt --
    api.register_tool(
        name="media_vault_hunt",
        description=(
            "Process a batch of entries from the media vault wishlist. "
            "Routes movies to Radarr, TV series to Sonarr, and concerts/audio "
            "to Prowlarr for direct search. Runs automatically every hour "
            "via heartbeat, or call manually for a batch."
        ),
        parameters={
            "type": "object",
            "properties": {
                "batch_size": {
                    "type": "integer",
                    "description": "Number of entries to process (default: 5).",
                    "default": 5,
                },
            },
        },
        handler=_media_vault_hunt,
        permission="none",
    )

    # -- media_vault_status --
    api.register_tool(
        name="media_vault_status",
        description=(
            "Show media vault collection progress — how many titles have "
            "been found, downloaded, or are still pending."
        ),
        parameters={"type": "object", "properties": {}},
        handler=_media_vault_status,
        permission="none",
    )

    # -- media_add_movie --
    api.register_tool(
        name="media_add_movie",
        description=(
            "Add a movie to Radarr by title or TMDB ID. Radarr will "
            "automatically search indexers and download the best match. "
            "Optionally specify a root folder (/data/Movies, /data/Documentaries, /data/Kids)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Movie title to search for.",
                },
                "tmdb_id": {
                    "type": "integer",
                    "description": "Optional: exact TMDB ID if known.",
                },
                "root_folder": {
                    "type": "string",
                    "description": "Root folder path (default: /data/Movies).",
                    "default": "/data/Movies",
                },
            },
            "required": ["title"],
        },
        handler=_media_add_movie,
        permission="none",
    )

    # -- media_add_series --
    api.register_tool(
        name="media_add_series",
        description=(
            "Add a TV series to Sonarr by title or TVDB ID. Sonarr will "
            "automatically search indexers and download all missing episodes. "
            "Optionally specify a root folder (/data/TV, /data/Kids)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Series title to search for.",
                },
                "tvdb_id": {
                    "type": "integer",
                    "description": "Optional: exact TVDB ID if known.",
                },
                "root_folder": {
                    "type": "string",
                    "description": "Root folder path (default: /data/TV).",
                    "default": "/data/TV",
                },
            },
            "required": ["title"],
        },
        handler=_media_add_series,
        permission="none",
    )

    # -- heartbeat hook for auto-hunting --
    api.register_hook("heartbeat_tick", _vault_hunt_heartbeat)

    tool_count = 8
    api.log(f"Loaded plugin hunter — {tool_count} tools, vault auto-hunt enabled")
