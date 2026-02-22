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

from server.plugins import PluginAPI

log = logging.getLogger("conduit.plugin.jellyfin-recs")

# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------
_JELLYFIN_URL = ""
_JELLYFIN_API_KEY = ""
_JELLYFIN_USER_ID = ""
_REFRESH_HOUR = 18
_ROW_COUNT = 6
_PROVIDER = "chatgpt"
_DATA_DIR = "~/.config/jellyfin-recs"


# ---------------------------------------------------------------------------
# Heartbeat hook — daily recommendation refresh (placeholder)
# ---------------------------------------------------------------------------
async def _on_heartbeat(**kwargs) -> dict | None:
    return None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def register(api: PluginAPI):
    global _JELLYFIN_URL, _JELLYFIN_API_KEY, _JELLYFIN_USER_ID
    global _REFRESH_HOUR, _ROW_COUNT, _PROVIDER, _DATA_DIR

    _JELLYFIN_URL = (api.config.get("jellyfin_url")
                     or os.environ.get("JELLYFIN_URL", _JELLYFIN_URL))

    # api_key uses env-var indirection: config value names the env var to read
    api_key_env = (api.config.get("api_key_env")
                   or os.environ.get("JELLYFIN_RECS_API_KEY_ENV", "JELLYFIN_API_KEY"))
    _JELLYFIN_API_KEY = os.environ.get(api_key_env, "")

    _JELLYFIN_USER_ID = (api.config.get("jellyfin_user_id")
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

    # -- heartbeat hook for daily refresh --
    api.register_hook("heartbeat_tick", _on_heartbeat)

    api.log(f"Loaded plugin jellyfin-recs — refresh at {_REFRESH_HOUR}:00, "
            f"provider={_PROVIDER}")
