"""LLM recommendation generator for Jellyfin Recommendations.

Sends the viewer profile and unwatched catalog to an LLM provider and
parses the structured JSON response into row definitions that the tvOS
app renders.

Usage::

    from plugins.jellyfin_recs.recommender import generate_recommendations

    rows = await generate_recommendations(
        profile=profile,
        unwatched=catalog,
        row_count=6,
        provider_name="chatgpt",
    )
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

log = logging.getLogger("conduit.plugin.jellyfin-recs.recommender")

# Maximum catalog items sent to the LLM to stay within token budgets
_MAX_CATALOG_ITEMS = 300


# ---------------------------------------------------------------------------
# Catalog formatting
# ---------------------------------------------------------------------------

def _build_catalog_text(unwatched: list[dict]) -> str:
    """Format unwatched catalog items as compact text for token efficiency.

    Each line: ``[{Id}] {Name} ({Year}) | {Type} | {genres} | {rating} | {overview[:100]}``

    Only the first ``_MAX_CATALOG_ITEMS`` items are included.
    """
    lines: list[str] = []
    for item in unwatched[:_MAX_CATALOG_ITEMS]:
        item_id = item.get("Id", "???")
        name = item.get("Name", "Unknown")
        year = item.get("ProductionYear") or "?"
        item_type = item.get("Type", "?")
        genres = ", ".join(item.get("Genres") or []) or "None"
        rating = item.get("CommunityRating")
        rating_str = f"{rating:.1f}" if rating is not None else "NR"
        overview = (item.get("Overview") or "")[:100].replace("\n", " ").strip()

        lines.append(f"[{item_id}] {name} ({year}) | {item_type} | {genres} | {rating_str} | {overview}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def build_prompt(profile: dict, unwatched: list[dict], row_count: int) -> str:
    """Build the full LLM prompt with viewer profile, context, and catalog.

    Parameters
    ----------
    profile:
        Output of ``build_profile()`` — viewer taste profile.
    unwatched:
        Raw Jellyfin catalog items (unwatched movies/series).
    row_count:
        Number of recommendation rows to generate.

    Returns
    -------
    str
        The complete user prompt for the LLM.
    """
    now = datetime.now(timezone.utc)
    day_name = now.strftime("%A")
    # Approximate local evening context — the heartbeat fires around 6pm
    time_context = "evening"

    # -- Profile sections --
    sections: list[str] = []

    # Top genres
    top_genres = profile.get("top_genres") or []
    if top_genres:
        genre_lines = [f"  - {g} ({c} watches)" for g, c in top_genres]
        sections.append("TOP GENRES:\n" + "\n".join(genre_lines))

    # Favorite people
    top_people = profile.get("top_people") or []
    if top_people:
        people_lines = [f"  - {name} ({role}, {count} titles)" for name, role, count in top_people]
        sections.append("FAVORITE PEOPLE:\n" + "\n".join(people_lines))

    # Recent watches
    recent = profile.get("recent_watches") or []
    if recent:
        recent_lines = []
        for w in recent[:10]:
            genres_str = ", ".join(w.get("genres") or [])
            rating = w.get("rating")
            rating_str = f" | {rating:.1f}" if rating else ""
            year = w.get("year") or "?"
            recent_lines.append(f"  - {w['name']} ({year}) [{genres_str}]{rating_str}")
        sections.append("RECENTLY WATCHED:\n" + "\n".join(recent_lines))

    # Binge series
    binge = profile.get("binge_series") or []
    if binge:
        sections.append("BINGE SERIES (3+ episodes watched):\n  - " + "\n  - ".join(binge[:8]))

    # Abandoned items
    abandoned = profile.get("abandoned") or []
    if abandoned:
        aband_lines = []
        for a in abandoned:
            series = f" ({a['series']})" if a.get("series") else ""
            aband_lines.append(
                f"  - {a['name']}{series} — {a['progress_pct']:.0f}% watched, "
                f"abandoned {a.get('days_ago', '?')} days ago"
            )
        sections.append("ABANDONED (started but stopped watching):\n" + "\n".join(aband_lines))

    # Catalog stats
    cat_summary = profile.get("catalog_summary") or {}
    if cat_summary:
        total = cat_summary.get("total", 0)
        avg = cat_summary.get("avg_rating")
        avg_str = f"{avg:.1f}" if avg else "N/A"
        top_cat_genres = cat_summary.get("by_genre") or []
        genre_breakdown = ", ".join(f"{g} ({c})" for g, c in top_cat_genres[:8])
        sections.append(
            f"UNWATCHED CATALOG STATS:\n"
            f"  Total: {total} | Avg rating: {avg_str}\n"
            f"  Genres: {genre_breakdown}"
        )

    profile_text = "\n\n".join(sections)

    # -- Catalog text --
    catalog_text = _build_catalog_text(unwatched)
    catalog_count = min(len(unwatched), _MAX_CATALOG_ITEMS)

    # -- Assemble the prompt --
    prompt = f"""You are a movie and TV recommendation engine. Analyse the viewer profile below and generate personalised recommendation rows from their unwatched library.

=== VIEWER PROFILE ===
{profile_text}

=== CONTEXT ===
Day: {day_name}
Time: {time_context}
This viewer will see these recommendations on their TV tonight.

=== UNWATCHED LIBRARY ({catalog_count} items) ===
{catalog_text}

=== INSTRUCTIONS ===
Generate exactly {row_count} recommendation rows. Each row is a themed collection of 4-8 items from the library above.

Rules:
1. Every itemId MUST come from the catalog above (use the exact ID in square brackets).
2. Vary row types — mix mood-based, theme-based, director/actor-focused, era-based, genre-blend, and discovery rows.
3. Consider the day ({day_name}) and time ({time_context}) — e.g. lighter fare on weeknights, deeper picks on weekends.
4. Factor in binge patterns — if the viewer binges series, suggest similar series they haven't started.
5. Reference abandoned items intelligently — don't recommend the same item, but acknowledge the taste signal.
6. Each row needs a catchy title and a brief reason explaining why these picks fit this viewer.
7. Prioritise highly-rated items but include some hidden gems (lower rating, matching taste).
8. Do NOT repeat the same item across multiple rows.

Return ONLY a JSON array. No markdown fences, no explanation, no extra text.

Each element:
{{"title": "Row Title", "reason": "Why this row fits", "itemIds": ["id1", "id2", ...], "type": "recommended"}}
"""
    return prompt


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_llm_response(raw: str, valid_ids: set[str]) -> list[dict]:
    """Parse the LLM JSON response into validated row dicts.

    Handles:
    - Markdown code fences (```json ... ```)
    - Validates that each row has a title and at least one valid item ID
    - Filters item IDs against the actual catalog
    - Skips rows with zero valid IDs
    """
    text = raw.strip()

    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        log.error("Failed to parse LLM response as JSON: %s", exc)
        log.debug("Raw response:\n%s", raw[:2000])
        return []

    if not isinstance(data, list):
        log.error("LLM response is not a JSON array (got %s)", type(data).__name__)
        return []

    rows: list[dict] = []
    seen_ids: set[str] = set()

    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            log.warning("Row %d is not a dict — skipping", i)
            continue

        title = entry.get("title")
        if not title or not isinstance(title, str):
            log.warning("Row %d missing title — skipping", i)
            continue

        raw_ids = entry.get("itemIds") or []
        if not isinstance(raw_ids, list):
            log.warning("Row %d itemIds is not a list — skipping", i)
            continue

        # Filter to valid, unseen IDs
        filtered_ids: list[str] = []
        for item_id in raw_ids:
            sid = str(item_id)
            if sid in valid_ids and sid not in seen_ids:
                filtered_ids.append(sid)
                seen_ids.add(sid)
            elif sid not in valid_ids:
                log.debug("Row %d: item ID %s not in catalog — dropped", i, sid)

        if not filtered_ids:
            log.warning("Row %d (%s) has no valid item IDs — skipping", i, title)
            continue

        rows.append({
            "title": title,
            "reason": entry.get("reason", ""),
            "itemIds": filtered_ids,
            "type": entry.get("type", "recommended"),
        })

    return rows


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate_recommendations(
    profile: dict,
    unwatched: list[dict],
    row_count: int = 6,
    provider_name: str | None = None,
) -> list[dict]:
    """Generate recommendation rows using an LLM provider.

    Parameters
    ----------
    profile:
        Viewer profile from ``build_profile()``.
    unwatched:
        Raw Jellyfin unwatched catalog items.
    row_count:
        Number of recommendation rows to request.
    provider_name:
        Conduit provider name (e.g. ``"chatgpt"``).  Falls back to the
        server default if ``None``.

    Returns
    -------
    list[dict]
        Validated recommendation rows, each with ``title``, ``reason``,
        ``itemIds``, and ``type`` keys.  Returns an empty list on failure.
    """
    # Lazy imports to avoid circular dependencies at module level
    from server.app import get_provider
    from server import db

    # Build the valid ID set from the catalog for validation
    valid_ids: set[str] = set()
    for item in unwatched:
        item_id = item.get("Id")
        if item_id:
            valid_ids.add(str(item_id))

    if not valid_ids:
        log.warning("Empty unwatched catalog — cannot generate recommendations")
        return []

    # Build prompt
    prompt = build_prompt(profile, unwatched, row_count)
    system = (
        "You are a personalised media recommendation engine. "
        "You return only valid JSON arrays — no markdown, no commentary. "
        "Every item ID in your response must exactly match one from the provided catalog."
    )

    # Call the LLM
    provider = get_provider(provider_name)
    log.info(
        "Generating %d recommendation rows via %s (%s) — catalog: %d items",
        row_count, provider.name, provider.model, len(valid_ids),
    )

    try:
        response_text, usage = await provider.generate(
            [{"role": "user", "content": prompt}],
            system=system,
        )
        await db.log_usage(
            provider.name, provider.model,
            usage.input_tokens, usage.output_tokens,
        )
        log.info(
            "LLM response: %d chars, %d input tokens, %d output tokens",
            len(response_text), usage.input_tokens, usage.output_tokens,
        )
    except Exception:
        log.exception("LLM generation failed")
        return []

    # Parse and validate
    rows = _parse_llm_response(response_text, valid_ids)
    log.info("Parsed %d valid recommendation rows (requested %d)", len(rows), row_count)

    return rows
