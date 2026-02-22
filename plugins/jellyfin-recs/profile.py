"""Viewer profile builder for Jellyfin Recommendations.

Takes raw Jellyfin API data (watch history, resume items, next-up episodes,
and the unwatched catalog) and computes a structured viewer profile that the
LLM recommender consumes.

Usage::

    from plugins.jellyfin_recs.profile import build_profile

    profile = build_profile(
        watch_history=history,
        resume_items=resume,
        next_up=next_up,
        unwatched_catalog=catalog,
    )
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PEOPLE_ROLES = {"Actor", "Director"}
_ABANDONED_DAYS = 7


def _parse_iso(date_str: str | None) -> datetime | None:
    """Parse an ISO-8601 datetime string from Jellyfin into a tz-aware UTC dt.

    Handles both ``2024-06-15T20:30:00.0000000Z`` (Jellyfin's 7-digit
    fractional seconds) and the more standard ``...+00:00`` / ``...Z``
    variants.  Returns *None* on bad / missing input.
    """
    if not date_str:
        return None
    # Normalise the trailing Z that Jellyfin uses
    s = date_str.replace("Z", "+00:00")
    # Truncate fractional seconds to 6 digits (Python < 3.11 barfs on 7)
    dot = s.rfind(".")
    if dot != -1:
        plus = s.find("+", dot)
        minus = s.find("-", dot)
        sep = plus if plus != -1 else minus
        if sep != -1:
            frac = s[dot + 1:sep][:6]
            s = s[:dot + 1] + frac + s[sep:]
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _display_name(item: dict) -> str:
    """Build a human-readable display name, using 'SeriesName - Name' for episodes."""
    series = item.get("SeriesName")
    name = item.get("Name", "Unknown")
    if series:
        return f"{series} - {name}"
    return name


def _progress_pct(item: dict) -> float:
    """Compute playback progress as a percentage (0-100)."""
    ud = item.get("UserData") or {}
    position = ud.get("PlaybackPositionTicks", 0) or 0
    runtime = item.get("RunTimeTicks") or 0
    if runtime <= 0:
        return 0.0
    return round(position / runtime * 100, 1)


def _days_since(date_str: str | None) -> int | None:
    """Return the number of whole days between *date_str* and now (UTC).

    Returns *None* if the date cannot be parsed.
    """
    dt = _parse_iso(date_str)
    if dt is None:
        return None
    delta = datetime.now(timezone.utc) - dt
    return max(int(delta.total_seconds() // 86400), 0)


# ---------------------------------------------------------------------------
# Profile sections
# ---------------------------------------------------------------------------

def _top_genres(watch_history: list[dict], limit: int = 10) -> list[tuple[str, int]]:
    """Return the top *limit* genres by frequency across the watch history."""
    counter: Counter[str] = Counter()
    for item in watch_history:
        for genre in (item.get("Genres") or []):
            counter[genre] += 1
    return counter.most_common(limit)


def _top_people(
    watch_history: list[dict],
    limit: int = 15,
    per_item_cap: int = 5,
) -> list[tuple[str, str, int]]:
    """Return the top *limit* actors/directors as (name, role, count).

    Only the first *per_item_cap* people (actors + directors) from each item
    are counted to avoid over-weighting large casts.
    """
    counter: Counter[tuple[str, str]] = Counter()
    for item in watch_history:
        counted = 0
        for person in (item.get("People") or []):
            if counted >= per_item_cap:
                break
            role = person.get("Type", "")
            if role not in _PEOPLE_ROLES:
                continue
            name = person.get("Name", "")
            if not name:
                continue
            counter[(name, role)] += 1
            counted += 1
    return [(name, role, count) for (name, role), count in counter.most_common(limit)]


def _recent_watches(watch_history: list[dict], limit: int = 20) -> list[dict]:
    """Return the last *limit* unique items from history, deduped by display name.

    Each entry contains: name, year, genres (top 3), rating, date.
    """
    seen: set[str] = set()
    result: list[dict] = []
    for item in watch_history:
        dname = _display_name(item)
        if dname in seen:
            continue
        seen.add(dname)

        ud = item.get("UserData") or {}
        genres = (item.get("Genres") or [])[:3]
        result.append({
            "name": dname,
            "year": item.get("ProductionYear"),
            "genres": genres,
            "rating": item.get("CommunityRating"),
            "date": ud.get("LastPlayedDate"),
        })
        if len(result) >= limit:
            break
    return result


def _split_resume(
    resume_items: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Split resume items into (active, abandoned) lists.

    *Abandoned* = last played more than ``_ABANDONED_DAYS`` days ago.
    Active items are everything else (including items with unparseable dates,
    which are treated as recent to avoid false positives).
    """
    active: list[dict] = []
    abandoned: list[dict] = []

    for item in resume_items:
        ud = item.get("UserData") or {}
        last_played = ud.get("LastPlayedDate")
        days = _days_since(last_played)
        progress = _progress_pct(item)
        entry = {
            "id": item.get("Id"),
            "name": item.get("Name", "Unknown"),
            "series": item.get("SeriesName"),
            "progress_pct": progress,
        }
        if days is not None and days >= _ABANDONED_DAYS:
            entry["days_ago"] = days
            abandoned.append(entry)
        else:
            active.append(entry)

    return active, abandoned


def _binge_series(watch_history: list[dict], threshold: int = 3) -> list[str]:
    """Return series names with *threshold*+ episodes in the watch history."""
    counter: Counter[str] = Counter()
    for item in watch_history:
        series = item.get("SeriesName")
        if series:
            counter[series] += 1
    return [name for name, count in counter.most_common() if count >= threshold]


def _next_up_list(next_up: list[dict]) -> list[dict]:
    """Normalise next-up items into a slim list of dicts."""
    return [
        {
            "id": item.get("Id"),
            "name": item.get("Name", "Unknown"),
            "series": item.get("SeriesName"),
        }
        for item in next_up
    ]


def _catalog_summary(unwatched_catalog: list[dict]) -> dict:
    """Summarise the unwatched catalog: total count, top genres, avg rating."""
    genre_counter: Counter[str] = Counter()
    ratings: list[float] = []

    for item in unwatched_catalog:
        for genre in (item.get("Genres") or []):
            genre_counter[genre] += 1
        cr = item.get("CommunityRating")
        if cr is not None:
            try:
                ratings.append(float(cr))
            except (ValueError, TypeError):
                pass

    avg = round(sum(ratings) / len(ratings), 2) if ratings else None

    return {
        "total": len(unwatched_catalog),
        "by_genre": genre_counter.most_common(15),
        "avg_rating": avg,
    }


def _profile_hash(watch_history: list[dict], resume_items: list[dict]) -> str:
    """Compute a deterministic change-detection hash (first 16 hex chars).

    Based on the ordered list of item IDs from history + resume.
    """
    ids: list[str] = []
    for item in watch_history:
        item_id = item.get("Id")
        if item_id:
            ids.append(str(item_id))
    for item in resume_items:
        item_id = item.get("Id")
        if item_id:
            ids.append(str(item_id))

    blob = json.dumps(ids, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_profile(
    watch_history: list[dict],
    resume_items: list[dict],
    next_up: list[dict],
    unwatched_catalog: list[dict],
) -> dict:
    """Build a structured viewer profile from raw Jellyfin API data.

    Parameters
    ----------
    watch_history:
        Items returned by ``JellyfinClient.get_watch_history``.
    resume_items:
        Items returned by ``JellyfinClient.get_resume_items``.
    next_up:
        Items returned by ``JellyfinClient.get_next_up``.
    unwatched_catalog:
        Items returned by ``JellyfinClient.get_unwatched_catalog``.

    Returns
    -------
    dict
        Profile with keys: ``top_genres``, ``top_people``,
        ``recent_watches``, ``abandoned``, ``binge_series``, ``resume``,
        ``next_up``, ``catalog_summary``, ``profile_hash``.
    """
    active_resume, abandoned = _split_resume(resume_items)

    return {
        "top_genres": _top_genres(watch_history),
        "top_people": _top_people(watch_history),
        "recent_watches": _recent_watches(watch_history),
        "abandoned": abandoned,
        "binge_series": _binge_series(watch_history),
        "resume": active_resume,
        "next_up": _next_up_list(next_up),
        "catalog_summary": _catalog_summary(unwatched_catalog),
        "profile_hash": _profile_hash(watch_history, resume_items),
    }
