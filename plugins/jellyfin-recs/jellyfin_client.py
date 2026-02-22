"""Jellyfin REST API client for the recommendation engine.

Wraps the subset of Jellyfin endpoints needed to fetch watch history,
in-progress items, next-up episodes, and the unwatched catalog.  All
methods return the ``Items`` list from the API response.

Usage::

    client = JellyfinClient(
        base_url="http://jellyfin:8096",
        api_key="<token>",
        user_id="<user-uuid>",
    )
    history = await client.get_watch_history(limit=100)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

log = logging.getLogger("conduit.plugin.jellyfin-recs.client")

# Shared field sets to avoid repetition across endpoints
_FULL_FIELDS = (
    "Genres,People,Studios,Tags,Overview,ProviderIds,"
    "CommunityRating,OfficialRating,ProductionYear"
)
_BRIEF_FIELDS = (
    "Overview,Genres,CommunityRating,OfficialRating,ProductionYear"
)


@dataclass
class JellyfinClient:
    """Async Jellyfin API client backed by httpx."""

    base_url: str
    api_key: str
    user_id: str
    timeout: float = 20.0

    # Private — built lazily on first request
    _client: httpx.AsyncClient = field(init=False, repr=False, default=None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url.rstrip("/"),
                headers={"X-Emby-Token": self.api_key},
                timeout=self.timeout,
            )
        return self._client

    async def _get(self, path: str, params: dict | None = None) -> dict:
        """Issue an authenticated GET and return the parsed JSON body."""
        client = self._ensure_client()
        resp = await client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def close(self) -> None:
        """Shut down the underlying HTTP connection pool."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_watch_history(self, limit: int = 200) -> list[dict]:
        """Return the user's most-recently-played movies and episodes."""
        data = await self._get(
            "/Items",
            params={
                "userId": self.user_id,
                "isPlayed": "true",
                "recursive": "true",
                "includeItemTypes": "Movie,Episode",
                "sortBy": "DatePlayed",
                "sortOrder": "Descending",
                "fields": _FULL_FIELDS,
                "enableUserData": "true",
                "limit": str(limit),
            },
        )
        return data.get("Items", [])

    async def get_resume_items(self, limit: int = 20) -> list[dict]:
        """Return items the user started but hasn't finished."""
        data = await self._get(
            "/UserItems/Resume",
            params={
                "userId": self.user_id,
                "mediaTypes": "Video",
                "limit": str(limit),
                "fields": _BRIEF_FIELDS,
                "enableUserData": "true",
            },
        )
        return data.get("Items", [])

    async def get_next_up(self, limit: int = 20) -> list[dict]:
        """Return the next unwatched episode for shows in progress."""
        data = await self._get(
            "/Shows/NextUp",
            params={
                "userId": self.user_id,
                "limit": str(limit),
                "fields": _BRIEF_FIELDS,
                "enableUserData": "true",
            },
        )
        return data.get("Items", [])

    async def get_unwatched_catalog(self, limit: int = 500) -> list[dict]:
        """Return unwatched movies and series sorted by community rating."""
        data = await self._get(
            "/Items",
            params={
                "userId": self.user_id,
                "isPlayed": "false",
                "recursive": "true",
                "includeItemTypes": "Movie,Series",
                "sortBy": "CommunityRating",
                "sortOrder": "Descending",
                "fields": _FULL_FIELDS,
                "enableUserData": "true",
                "limit": str(limit),
            },
        )
        return data.get("Items", [])

    async def get_items_by_ids(self, item_ids: list[str]) -> list[dict]:
        """Fetch full metadata for a specific set of item IDs."""
        if not item_ids:
            return []
        data = await self._get(
            "/Items",
            params={
                "userId": self.user_id,
                "ids": ",".join(item_ids),
                "fields": _BRIEF_FIELDS,
                "enableUserData": "true",
                "enableImages": "true",
            },
        )
        return data.get("Items", [])
