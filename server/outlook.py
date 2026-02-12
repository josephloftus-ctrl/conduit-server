"""Outlook email client — MSAL auth + Microsoft Graph API."""

import json
import logging
from pathlib import Path

import httpx
import msal

from . import config

log = logging.getLogger("conduit.outlook")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["Mail.Read", "User.Read"]

_TOKEN_CACHE_PATH = Path(__file__).parent / ".outlook_token_cache.bin"
_app: msal.PublicClientApplication | None = None
_cache: msal.SerializableTokenCache | None = None


def _get_app() -> msal.PublicClientApplication | None:
    """Get or create the MSAL app with persistent token cache."""
    global _app, _cache

    if not config.OUTLOOK_CLIENT_ID:
        return None

    if _app is not None:
        return _app

    _cache = msal.SerializableTokenCache()
    if _TOKEN_CACHE_PATH.exists():
        _cache.deserialize(_TOKEN_CACHE_PATH.read_text())

    _app = msal.PublicClientApplication(
        config.OUTLOOK_CLIENT_ID,
        authority=AUTHORITY,
        token_cache=_cache,
    )
    return _app


def _save_cache():
    """Persist token cache to disk if state changed."""
    if _cache and _cache.has_state_changed:
        _TOKEN_CACHE_PATH.write_text(_cache.serialize())
        _cache.has_state_changed = False


def get_access_token() -> str | None:
    """Acquire token silently from cache. Returns None if re-auth needed."""
    app = _get_app()
    if not app:
        return None

    accounts = app.get_accounts()
    if not accounts:
        return None

    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    _save_cache()

    if result and "access_token" in result:
        return result["access_token"]

    return None


def is_configured() -> bool:
    """Check if Outlook client ID is set."""
    return bool(config.OUTLOOK_CLIENT_ID)


async def get_inbox(count: int = 10, unread_only: bool = False) -> list[dict]:
    """Fetch inbox messages from Microsoft Graph."""
    token = get_access_token()
    if not token:
        return []

    url = f"{GRAPH_BASE}/me/mailFolders/inbox/messages"
    params = {
        "$top": str(count),
        "$select": "id,subject,from,receivedDateTime,isRead,bodyPreview",
        "$orderby": "receivedDateTime desc",
    }
    if unread_only:
        params["$filter"] = "isRead eq false"

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                url, params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
    except Exception as e:
        log.error("Graph API inbox error: %s", e)
        return []

    data = resp.json()
    return data.get("value", [])


async def search_messages(query: str, count: int = 10) -> list[dict]:
    """Search messages using Microsoft Graph $search."""
    token = get_access_token()
    if not token:
        return []

    url = f"{GRAPH_BASE}/me/messages"
    params = {
        "$search": f'"{query}"',
        "$top": str(count),
        "$select": "id,subject,from,receivedDateTime,isRead,bodyPreview",
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                url, params=params,
                headers={
                    "Authorization": f"Bearer {token}",
                    "ConsistencyLevel": "eventual",
                },
            )
            resp.raise_for_status()
    except Exception as e:
        log.error("Graph API search error: %s", e)
        return []

    data = resp.json()
    return data.get("value", [])


async def get_message(message_id: str) -> dict | None:
    """Fetch a single message with full body."""
    token = get_access_token()
    if not token:
        return None

    url = f"{GRAPH_BASE}/me/messages/{message_id}"
    params = {"$select": "id,subject,from,toRecipients,receivedDateTime,body,isRead"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                url, params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
    except Exception as e:
        log.error("Graph API message error: %s", e)
        return None

    return resp.json()


async def get_unread_count() -> int:
    """Get unread message count for inbox."""
    token = get_access_token()
    if not token:
        return 0

    url = f"{GRAPH_BASE}/me/mailFolders/inbox"
    params = {"$select": "unreadItemCount"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                url, params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
    except Exception as e:
        log.error("Graph API unread count error: %s", e)
        return 0

    data = resp.json()
    return data.get("unreadItemCount", 0)
