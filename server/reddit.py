"""Reddit activity scraper + digest generator for the worker loop."""

import json
import logging
from pathlib import Path

import httpx

from . import config

log = logging.getLogger("conduit.reddit")

_USER_AGENT = "conduit-worker/1.0 (by u/kitchenjesus)"
_BASE_URL = "https://www.reddit.com/user"


async def fetch_activity(username: str, limit: int = 100) -> list[dict]:
    """Fetch public Reddit activity for a user.

    Returns list of dicts with: subreddit, type, title, body, score, created_utc.
    Paginates to get up to 2 pages (200 items).
    """
    items: list[dict] = []
    after: str | None = None
    pages = 2 if limit >= 100 else 1

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for _ in range(pages):
            params: dict = {"limit": min(limit, 100), "raw_json": 1}
            if after:
                params["after"] = after

            url = f"{_BASE_URL}/{username}.json"
            resp = await client.get(
                url,
                params=params,
                headers={"User-Agent": _USER_AGENT},
            )
            if resp.status_code != 200:
                log.warning("Reddit fetch failed (%d): %s", resp.status_code, resp.text[:200])
                break

            data = resp.json().get("data", {})
            children = data.get("children", [])
            if not children:
                break

            for child in children:
                kind = child.get("kind", "")
                d = child.get("data", {})
                items.append({
                    "subreddit": d.get("subreddit", ""),
                    "type": "comment" if kind == "t1" else "post",
                    "title": d.get("link_title", d.get("title", "")),
                    "body": (d.get("body", "") or d.get("selftext", ""))[:500],
                    "score": d.get("score", 0),
                    "created_utc": d.get("created_utc", 0),
                })

            after = data.get("after")
            if not after:
                break

    log.info("Fetched %d Reddit items for u/%s", len(items), username)
    return items


async def generate_digest(activity: list[dict], provider) -> dict:
    """Summarize Reddit activity into a structured digest using an LLM.

    Args:
        activity: List of activity items from fetch_activity().
        provider: A Conduit provider instance (must have .generate()).

    Returns the digest dict (also written to disk).
    """
    if not activity:
        return {"summary": "No recent activity found.", "interests": [], "activity": []}

    # Group by subreddit for the prompt
    by_sub: dict[str, list[dict]] = {}
    for item in activity:
        sub = item["subreddit"] or "unknown"
        by_sub.setdefault(sub, []).append(item)

    sub_summary = []
    for sub, items in sorted(by_sub.items(), key=lambda x: -len(x[1])):
        types = {"comment": 0, "post": 0}
        total_score = 0
        for it in items:
            types[it["type"]] += 1
            total_score += it["score"]
        sub_summary.append(
            f"r/{sub}: {len(items)} items ({types['post']} posts, {types['comment']} comments), "
            f"total score {total_score}"
        )

    # Sample some high-engagement items for context
    top_items = sorted(activity, key=lambda x: -x["score"])[:10]
    samples = []
    for it in top_items:
        body_preview = it["body"][:200] if it["body"] else "(no text)"
        samples.append(f"[r/{it['subreddit']}] ({it['type']}, score {it['score']}) {it['title']}: {body_preview}")

    prompt = f"""Analyze this Reddit user's recent activity and produce a JSON digest.

Subreddit breakdown:
{chr(10).join(sub_summary)}

Top engagement items:
{chr(10).join(samples)}

Return a JSON object with these keys:
- "top_interests": list of 3-5 main interest areas
- "hot_topics": list of 2-3 topics they're currently opinionated about
- "emerging_interests": list of 0-2 new/unusual subreddits or topics
- "mood": one-sentence sentiment/mood read
- "summary": 2-3 sentence overview of their recent activity

Return ONLY valid JSON, no markdown fences."""

    try:
        response, _usage = await provider.generate(
            [{"role": "user", "content": prompt}],
            system="You are a concise analyst. Return only valid JSON.",
        )

        # Parse the JSON response (strip markdown fences if present)
        text = response.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        digest = json.loads(text)
    except Exception as e:
        log.error("Digest generation failed: %s", e)
        digest = {
            "summary": f"Activity digest generation failed: {e}",
            "top_interests": [],
            "hot_topics": [],
            "emerging_interests": [],
            "mood": "unknown",
        }

    # Add metadata
    digest["item_count"] = len(activity)
    digest["subreddit_count"] = len(by_sub)
    digest["generated_at"] = __import__("time").time()

    # Write to disk
    _write_digest(digest)
    return digest


def _write_digest(digest: dict) -> None:
    """Write digest to the worker data directory."""
    data_dir = Path(config.WORKER_DATA_DIR).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "reddit_digest.json"
    path.write_text(json.dumps(digest, indent=2))
    log.info("Reddit digest written to %s", path)


async def refresh_digest() -> dict:
    """Full pipeline: fetch activity + generate digest. Called by scheduler."""
    username = config.WORKER_REDDIT_USERNAME
    if not username:
        log.warning("No Reddit username configured for worker")
        return {}

    activity = await fetch_activity(username)
    if not activity:
        return {}

    # Get the ideation provider
    from .app import get_provider
    provider = get_provider(config.WORKER_IDEATION_PROVIDER)
    if not provider:
        provider = get_provider(None)  # fallback to default

    return await generate_digest(activity, provider)
