"""Hunter plugin — search and download torrents via Jackett + qBittorrent.

Tools:
- torrent_search: Search Jackett indexers, return ranked results with magnets
- torrent_download: Send a magnet link to qBittorrent and record in history
- torrent_status: List active/completed downloads from qBittorrent
- torrent_history: Show downloads grabbed through the assistant
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

import httpx

from server.plugins import PluginAPI

log = logging.getLogger("conduit.plugin.hunter")

# ---------------------------------------------------------------------------
# Defaults (overridable via env vars)
# ---------------------------------------------------------------------------
_JACKETT_URL = "http://localhost:9117"
_JACKETT_API_KEY = ""
_QBIT_URL = "http://localhost:8080"
_QBIT_USER = "admin"
_QBIT_PASS = "adminadmin"
_MIN_SEEDERS = 5
_DATA_DIR = "~/.config/hunter"

# Torznab category codes
_CATEGORIES = {
    "movies": "2000",
    "tv": "5000",
    "music": "3000",
    "books": "7000",
    "software": "4000",
    "isos": "4000",
}

_TORZNAB_NS = "http://torznab.com/schemas/2015/feed"

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_qbit_sid: str | None = None
_db: _HunterDB | None = None


# ---------------------------------------------------------------------------
# SQLite history
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
        self._conn.commit()

    def add(self, info_hash: str, name: str, category: str, source: str,
            size: int, seeders: int) -> bool:
        """Record a grab. Returns False if duplicate hash."""
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


def _get_db() -> _HunterDB:
    global _db
    if _db is None:
        data_dir = Path(_DATA_DIR).expanduser()
        data_dir.mkdir(parents=True, exist_ok=True)
        _db = _HunterDB(data_dir / "hunter.db")
    return _db


# ---------------------------------------------------------------------------
# Jackett / Torznab
# ---------------------------------------------------------------------------
def _parse_torznab(xml_text: str) -> list[dict]:
    """Parse Torznab XML response into result dicts."""
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
        peers = 0
        info_hash = None
        magnet = None

        for attr in item.findall(f"{{{_TORZNAB_NS}}}attr"):
            name = attr.get("name", "")
            value = attr.get("value", "")
            if name == "seeders":
                seeders = int(value)
            elif name == "peers":
                peers = int(value)
            elif name == "infohash":
                info_hash = value
            elif name == "magneturl":
                magnet = value

        # Fall back: link itself may be a magnet
        if not magnet and link.startswith("magnet:"):
            magnet = link

        # Extract infohash from magnet if not in attrs
        if not info_hash and magnet:
            m = re.search(r"btih:([a-fA-F0-9]{40})", magnet)
            if m:
                info_hash = m.group(1)

        results.append({
            "title": title,
            "link": link,
            "magnet": magnet,
            "size": int(size_text) if size_text.isdigit() else 0,
            "seeders": seeders,
            "peers": peers,
            "info_hash": info_hash,
            "source": source,
        })

    return results


async def _jackett_search(query: str, category: str | None = None) -> list[dict]:
    """Search Jackett and return parsed, filtered, ranked results."""
    if not _JACKETT_API_KEY:
        return []

    params: dict = {
        "apikey": _JACKETT_API_KEY,
        "t": "search",
        "q": query,
    }
    if category and category.lower() in _CATEGORIES:
        params["cat"] = _CATEGORIES[category.lower()]

    url = f"{_JACKETT_URL}/api/v2.0/indexers/all/results/torznab/api"

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()

    results = _parse_torznab(resp.text)

    # Filter by min seeders
    results = [r for r in results if r["seeders"] >= _MIN_SEEDERS]

    # Rank by seeders descending
    results.sort(key=lambda r: r["seeders"], reverse=True)

    return results


# ---------------------------------------------------------------------------
# qBittorrent API
# ---------------------------------------------------------------------------
async def _qbit_login(client: httpx.AsyncClient) -> str | None:
    """Authenticate with qBittorrent, return SID cookie or None."""
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
    """Make an authenticated qBittorrent API request."""
    global _qbit_sid

    url = f"{_QBIT_URL}{endpoint}"
    headers = {"Referer": _QBIT_URL}

    async with httpx.AsyncClient(timeout=15) as client:
        # Try with existing session first
        if _qbit_sid:
            client.cookies.set("SID", _qbit_sid)

        resp = await client.request(method, url, headers=headers, **kwargs)

        # If 403, re-login and retry
        if resp.status_code == 403:
            sid = await _qbit_login(client)
            if not sid:
                resp.raise_for_status()
            client.cookies.set("SID", sid)
            resp = await client.request(method, url, headers=headers, **kwargs)

        resp.raise_for_status()
        return resp


async def _qbit_add_torrent(magnet: str, category: str = "") -> bool:
    """Send a magnet link to qBittorrent. Returns True on success."""
    data = {"urls": magnet}
    if category:
        data["category"] = category
    resp = await _qbit_request("POST", "/api/v2/torrents/add", data=data)
    return resp.status_code == 200


async def _qbit_list_torrents(filter_: str = "all") -> list[dict]:
    """List torrents from qBittorrent."""
    resp = await _qbit_request("GET", "/api/v2/torrents/info", params={"filter": filter_})
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
# Tool handlers
# ---------------------------------------------------------------------------
async def _torrent_search(query: str, category: str | None = None) -> str:
    """Search for torrents via Jackett."""
    if not _JACKETT_API_KEY:
        return "Error: HUNTER_JACKETT_API_KEY not configured."

    try:
        results = await _jackett_search(query, category)
    except httpx.HTTPStatusError as e:
        return f"Error: Jackett returned {e.response.status_code}."
    except httpx.ConnectError:
        return "Error: Cannot connect to Jackett. Is it running?"
    except Exception as e:
        return f"Error searching Jackett: {e}"

    if not results:
        return f"No results found for \"{query}\" with at least {_MIN_SEEDERS} seeders."

    # Cap at 15 results for readability
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
    """Send a magnet link to qBittorrent for download."""
    if not magnet.startswith("magnet:"):
        return "Error: Invalid magnet link — must start with 'magnet:'."

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

    # Record in history if we can extract the info hash
    info_hash = None
    m = re.search(r"btih:([a-fA-F0-9]{40})", magnet)
    if m:
        info_hash = m.group(1)

    display_name = name or "unknown"
    if info_hash:
        db = _get_db()
        if db.has_hash(info_hash):
            return f"Sent to qBittorrent: {display_name} (already in history, possible re-download)."
        db.add(
            info_hash=info_hash,
            name=display_name,
            category=category or "uncategorized",
            source="conduit",
            size=0,
            seeders=0,
        )

    cat_note = f" [{category}]" if category else ""
    return f"Sent to qBittorrent: {display_name}{cat_note}"


async def _torrent_status(filter: str = "all") -> str:
    """List active/completed torrents from qBittorrent."""
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
    """Show recent downloads grabbed through the assistant."""
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


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def register(api: PluginAPI):
    global _JACKETT_URL, _JACKETT_API_KEY, _QBIT_URL, _QBIT_USER, _QBIT_PASS
    global _MIN_SEEDERS, _DATA_DIR

    # Config from api.config with env var fallbacks
    _JACKETT_URL = (api.config.get("jackett_url")
                    or os.environ.get("HUNTER_JACKETT_URL", _JACKETT_URL))
    _JACKETT_API_KEY = (api.config.get("jackett_api_key")
                        or os.environ.get("HUNTER_JACKETT_API_KEY", _JACKETT_API_KEY))
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

    if not _JACKETT_API_KEY:
        api.log("Hunter plugin: HUNTER_JACKETT_API_KEY not set — search will be disabled",
                level="warning")

    # -- torrent_search --
    api.register_tool(
        name="torrent_search",
        description=(
            "Search for torrents across indexers via Jackett. Returns ranked "
            "results with title, size, seeders, source, and magnet links. "
            "Optional category filter: movies, tv, music, books, software."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (e.g. 'Ubuntu 24.04', 'The Matrix 1999').",
                },
                "category": {
                    "type": "string",
                    "description": "Optional category filter: movies, tv, music, books, software.",
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
            "Provide the full magnet URI from a torrent_search result. "
            "Optionally specify a category (e.g. 'movies', 'tv') and "
            "a human-readable name for history tracking."
        ),
        parameters={
            "type": "object",
            "properties": {
                "magnet": {
                    "type": "string",
                    "description": "Full magnet link starting with 'magnet:?xt=urn:btih:...'.",
                },
                "category": {
                    "type": "string",
                    "description": "Download category (e.g. 'movies', 'tv'). Used by qBittorrent for organization.",
                },
                "name": {
                    "type": "string",
                    "description": "Human-readable name for the download (for history tracking).",
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
            "List torrents in qBittorrent with their status, progress, "
            "speed, and size. Filter by: all, downloading, seeding, "
            "completed, paused, active, inactive, stalled, errored."
        ),
        parameters={
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "description": "Filter torrents by status (default: 'all').",
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
        description=(
            "Show recent downloads that were grabbed through the assistant. "
            "Displays name, category, size, and when it was grabbed."
        ),
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max number of history entries to show (default: 20).",
                    "default": 20,
                },
            },
        },
        handler=_torrent_history,
        permission="none",
    )

    tool_count = 4
    api.log(f"Loaded plugin hunter — {tool_count} tools")
