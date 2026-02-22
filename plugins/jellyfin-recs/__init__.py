"""Jellyfin Recommendations plugin — AI-powered movie and TV recommendations.

Analyses Jellyfin watch history to build a viewing profile, then uses an LLM
to generate personalised recommendations.  Results are cached and refreshed
daily via the heartbeat hook.

Hooks:
- heartbeat_tick: triggers a daily recommendation refresh
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from server.plugins import PluginAPI

from .jellyfin_client import JellyfinClient
from .profile import build_profile
from .recommender import generate_recommendations
from .cache import RecsCache

log = logging.getLogger("conduit.plugin.jellyfin-recs")

# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------
_JELLYFIN_URL = "http://localhost:8096"
_JELLYFIN_API_KEY = ""
_JELLYFIN_USER_ID = ""
_REFRESH_HOUR = 18
_ROW_COUNT = 6
_PROVIDER = "chatgpt"
_DATA_DIR = "~/.config/jellyfin-recs"

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------
_client: JellyfinClient | None = None
_cache: RecsCache | None = None
_last_check_date: str = ""


# ---------------------------------------------------------------------------
# Heartbeat hook — daily recommendation refresh
# ---------------------------------------------------------------------------
async def _on_heartbeat(**kwargs) -> dict | None:
    """Check if it's time for the daily recommendation refresh."""
    global _last_check_date

    if not _client or not _cache:
        return None

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # Already ran today
    if _last_check_date == today:
        return None

    # Not yet time
    if now.hour < _REFRESH_HOUR:
        return None

    _last_check_date = today
    log.info("Starting daily recommendation refresh")

    try:
        result = await _refresh_recs()
        if result:
            log.info("Generated %d recommendation rows", len(result))
            try:
                from server import ntfy
                await ntfy.push(
                    title="Recommendations Updated",
                    body=f"Generated {len(result)} rows for tonight.",
                    tags=["tv", "sparkles"],
                    priority=2,
                )
            except Exception:
                pass
    except Exception as e:
        log.error("Daily refresh failed: %s", e)

    return None


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------
async def _refresh_recs() -> list[dict] | None:
    """Run the full recommendation pipeline."""
    if not _client or not _cache:
        return None

    # 1. Gather data from Jellyfin
    log.info("Fetching data from Jellyfin...")
    history = await _client.get_watch_history()
    resume = await _client.get_resume_items()
    next_up = await _client.get_next_up()
    catalog = await _client.get_unwatched_catalog()

    log.info("Fetched %d history, %d resume, %d next_up, %d catalog items",
             len(history), len(resume), len(next_up), len(catalog))

    # 2. Build profile
    profile = build_profile(history, resume, next_up, catalog)

    # 3. Check if we need to regenerate
    if not _cache.needs_refresh(_JELLYFIN_USER_ID, profile["profile_hash"]):
        log.info("Cache still fresh, skipping LLM call")
        latest = _cache.get_latest(_JELLYFIN_USER_ID)
        return latest["rows"] if latest else None

    # 4. Generate AI recommendations
    ai_rows = await generate_recommendations(
        profile, catalog, row_count=_ROW_COUNT, provider_name=_PROVIDER
    )

    # 5. Prepend system rows (Continue Watching, Next Up)
    rows: list[dict] = []
    if profile["resume"]:
        rows.append({
            "title": "Continue Watching",
            "reason": "Pick up where you left off",
            "itemIds": [r["id"] for r in profile["resume"] if r.get("id")],
            "type": "resume",
        })
    if profile["next_up"]:
        rows.append({
            "title": "Next Up",
            "reason": "Next episodes in your series",
            "itemIds": [n["id"] for n in profile["next_up"] if n.get("id")],
            "type": "nextup",
        })
    rows.extend(ai_rows)

    # 6. Cache the results
    _cache.store(_JELLYFIN_USER_ID, profile["profile_hash"], rows)
    return rows


# ---------------------------------------------------------------------------
# Public API for REST endpoint
# ---------------------------------------------------------------------------
def get_cached_recs(user_id: str = "") -> dict | None:
    """Return cached recommendations for the REST endpoint.

    Returns {rows, generated_at, stale} or None.
    """
    uid = user_id or _JELLYFIN_USER_ID
    if not _cache or not uid:
        return None
    latest = _cache.get_latest(uid)
    if not latest:
        return None
    return {
        "rows": latest["rows"],
        "generated_at": latest["generated_at"],
        "stale": False,
    }


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------
async def _jellyfin_recs_refresh(**kwargs) -> str:
    """Manually trigger a recommendation refresh."""
    try:
        result = await _refresh_recs()
        if result is None:
            return "Plugin not configured — check Jellyfin API key and user ID."
        return f"Generated {len(result)} recommendation rows."
    except Exception as e:
        return f"Refresh failed: {e}"


async def _jellyfin_recs_status(**kwargs) -> str:
    """Show current recommendation status."""
    if not _cache:
        return "Plugin not configured."
    latest = _cache.get_latest(_JELLYFIN_USER_ID)
    if not latest:
        return "No recommendations cached yet. Run a refresh first."
    row_count = len(latest["rows"])
    row_titles = [r.get("title", "?") for r in latest["rows"]]
    return (
        f"Last generated: {latest['generated_at']}\n"
        f"Profile hash: {latest['profile_hash']}\n"
        f"Rows ({row_count}): {', '.join(row_titles)}"
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def register(api: PluginAPI):
    global _JELLYFIN_URL, _JELLYFIN_API_KEY, _JELLYFIN_USER_ID
    global _REFRESH_HOUR, _ROW_COUNT, _PROVIDER, _DATA_DIR
    global _client, _cache

    _JELLYFIN_URL = (api.config.get("jellyfin_url") or api.config.get("base_url")
                     or os.environ.get("JELLYFIN_URL", _JELLYFIN_URL))

    # api_key uses env-var indirection: config value names the env var to read
    api_key_env = (api.config.get("api_key_env")
                   or os.environ.get("JELLYFIN_RECS_API_KEY_ENV", "JELLYFIN_API_KEY"))
    _JELLYFIN_API_KEY = os.environ.get(api_key_env, "")

    _JELLYFIN_USER_ID = (api.config.get("jellyfin_user_id") or api.config.get("user_id")
                         or os.environ.get("JELLYFIN_USER_ID", _JELLYFIN_USER_ID))

    _REFRESH_HOUR = int(api.config.get("refresh_hour")
                        or os.environ.get("JELLYFIN_RECS_REFRESH_HOUR", _REFRESH_HOUR))
    _ROW_COUNT = int(api.config.get("row_count")
                     or os.environ.get("JELLYFIN_RECS_ROW_COUNT", _ROW_COUNT))
    _PROVIDER = (api.config.get("provider")
                 or os.environ.get("JELLYFIN_RECS_PROVIDER", _PROVIDER))
    _DATA_DIR = (api.config.get("data_dir")
                 or os.environ.get("JELLYFIN_RECS_DATA_DIR", _DATA_DIR))

    if not _JELLYFIN_API_KEY:
        api.log("Jellyfin API key not set — recommendations disabled",
                level="warning")
    if not _JELLYFIN_USER_ID:
        api.log("Jellyfin user ID not set — recommendations disabled",
                level="warning")

    # Initialize client and cache if credentials are present
    if _JELLYFIN_API_KEY and _JELLYFIN_USER_ID:
        _client = JellyfinClient(
            base_url=_JELLYFIN_URL,
            api_key=_JELLYFIN_API_KEY,
            user_id=_JELLYFIN_USER_ID,
        )
        _cache = RecsCache(_DATA_DIR)

    # -- Hooks --
    api.register_hook("heartbeat_tick", _on_heartbeat)

    # -- Tools --
    api.register_tool(
        name="jellyfin_recs_refresh",
        description=(
            "Manually trigger a Jellyfin recommendation refresh. Pulls watch "
            "history, builds viewer profile, calls LLM for new recommendations."
        ),
        parameters={"type": "object", "properties": {}},
        handler=_jellyfin_recs_refresh,
        permission="none",
    )
    api.register_tool(
        name="jellyfin_recs_status",
        description=(
            "Show the current state of cached Jellyfin recommendations — "
            "last generation time, row count, row titles."
        ),
        parameters={"type": "object", "properties": {}},
        handler=_jellyfin_recs_status,
        permission="none",
    )

    api.log(f"Loaded plugin jellyfin-recs — refresh at {_REFRESH_HOUR}:00, "
            f"provider={_PROVIDER}")
