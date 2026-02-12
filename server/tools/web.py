"""Web tools — search via SearXNG and fetch URL content."""

import asyncio
import logging
import time
from urllib.parse import urlparse

import httpx
import trafilatura

from . import register
from .definitions import ToolDefinition
from .. import config

log = logging.getLogger("conduit.tools.web")

MAX_FETCH_SIZE = 30 * 1024  # 30KB truncation limit

# In-memory cache for deep search page fetches: {url: (html, timestamp)}
_page_cache: dict[str, tuple[str, float]] = {}


async def _web_search(query: str, num_results: int = 5) -> str:
    """Search the web via local SearXNG instance."""
    if not config.WEB_SEARCH_ENABLED:
        return "Error: Web search is disabled in config."

    url = f"{config.SEARXNG_URL.rstrip('/')}/search"
    params = {"q": query, "format": "json"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
    except httpx.ConnectError:
        return "Error: SearXNG is not reachable. Is the container running?"
    except httpx.HTTPStatusError as e:
        return f"Error: SearXNG returned {e.response.status_code}"
    except Exception as e:
        return f"Error: Web search failed — {e}"

    data = resp.json()
    results = data.get("results", [])[:num_results]

    if not results:
        return f"No results found for: {query}"

    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "Untitled")
        link = r.get("url", "")
        snippet = r.get("content", "")
        lines.append(f"{i}. **{title}**")
        lines.append(f"   {link}")
        if snippet:
            lines.append(f"   {snippet}")
        lines.append("")

    return "\n".join(lines).strip()


async def _web_fetch(url: str) -> str:
    """Fetch a URL and extract readable text content."""
    try:
        async with httpx.AsyncClient(
            timeout=config.WEB_FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Conduit/1.0)"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except httpx.ConnectError:
        return f"Error: Could not connect to {url}"
    except httpx.HTTPStatusError as e:
        return f"Error: {url} returned {e.response.status_code}"
    except httpx.TimeoutException:
        return f"Error: Request to {url} timed out"
    except Exception as e:
        return f"Error: Failed to fetch {url} — {e}"

    html = resp.text

    # Try trafilatura extraction (best for articles)
    content = trafilatura.extract(html, include_links=True)
    if not content:
        # Fallback: strip HTML tags for basic text extraction
        import re
        content = re.sub(r"<[^>]+>", " ", html)
        content = re.sub(r"\s+", " ", content).strip()
        if not content:
            return f"Error: Could not extract text content from {url}"

    if len(content) > MAX_FETCH_SIZE:
        content = content[:MAX_FETCH_SIZE] + "\n\n... [truncated at 30KB]"

    return f"Content from {url}:\n\n{content}"


def _extract_and_chunk(html: str, url: str, max_chunks: int = 20) -> list[dict]:
    """Extract text from HTML and split into ranked chunks."""
    content = trafilatura.extract(html, include_links=False)
    if not content:
        return []

    page_title = urlparse(url).netloc

    # Split on double newlines (paragraph boundaries)
    raw_chunks = [p.strip() for p in content.split("\n\n") if p.strip()]

    # Merge small adjacent chunks to hit ~300-500 char minimum
    merged = []
    buf = ""
    for chunk in raw_chunks:
        if buf:
            buf += "\n\n" + chunk
        else:
            buf = chunk
        if len(buf) >= 300:
            merged.append(buf)
            buf = ""
    if buf:
        # Attach remainder to last chunk or keep as-is
        if merged and len(buf) < 150:
            merged[-1] += "\n\n" + buf
        else:
            merged.append(buf)

    return [{"text": c, "url": url, "title": page_title} for c in merged[:max_chunks]]


def _score_chunks(query: str, chunks: list[dict]) -> list[dict]:
    """BM25-style keyword scoring — rank chunks by query term overlap density."""
    query_terms = set(query.lower().split())
    if not query_terms:
        return chunks

    scored = []
    for chunk in chunks:
        text_lower = chunk["text"].lower()
        words = text_lower.split()
        if not words:
            continue

        matched_terms = sum(1 for t in query_terms if t in text_lower)
        term_ratio = matched_terms / len(query_terms)

        # Term frequency density: how often query terms appear relative to chunk size
        term_hits = sum(1 for w in words if w in query_terms)
        density = term_hits / len(words)

        score = round(term_ratio * 0.7 + density * 0.3, 3)
        scored.append({**chunk, "score": score})

    scored.sort(key=lambda c: c["score"], reverse=True)
    return scored


async def _fetch_cached(url: str) -> str | None:
    """Fetch a URL with in-memory TTL cache. Returns raw HTML or None on failure."""
    now = time.time()
    ttl = config.DEEP_SEARCH_CACHE_TTL

    # Check cache
    if url in _page_cache:
        html, ts = _page_cache[url]
        if now - ts < ttl:
            return html

    try:
        async with httpx.AsyncClient(
            timeout=config.WEB_FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Conduit/1.0)"},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except Exception as e:
        log.debug("Deep search fetch failed for %s: %s", url, e)
        return None

    html = resp.text
    _page_cache[url] = (html, now)
    return html


async def _web_search_deep(query: str, num_pages: int = 3, num_chunks: int = 10) -> str:
    """Deep search: fetch top pages, extract content, rank chunks by relevance."""
    if not config.WEB_SEARCH_ENABLED:
        return "Error: Web search is disabled in config."

    num_pages = min(num_pages, config.DEEP_SEARCH_MAX_PAGES)
    num_chunks = min(num_chunks, config.DEEP_SEARCH_MAX_CHUNKS)

    # Step 1: SearXNG query to get URLs
    url = f"{config.SEARXNG_URL.rstrip('/')}/search"
    params = {"q": query, "format": "json"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
    except httpx.ConnectError:
        return "Error: SearXNG is not reachable. Is the container running?"
    except httpx.HTTPStatusError as e:
        return f"Error: SearXNG returned {e.response.status_code}"
    except Exception as e:
        return f"Error: Deep search failed — {e}"

    data = resp.json()
    results = data.get("results", [])
    if not results:
        return f"No results found for: {query}"

    # Step 2: Collect top page URLs
    urls = []
    for r in results:
        page_url = r.get("url", "")
        if page_url and page_url not in urls:
            urls.append(page_url)
        if len(urls) >= num_pages:
            break

    # Step 3: Fetch all pages in parallel
    html_results = await asyncio.gather(*[_fetch_cached(u) for u in urls])

    # Step 4: Extract and chunk each page
    all_chunks = []
    for page_url, html in zip(urls, html_results):
        if html is None:
            continue
        chunks = _extract_and_chunk(html, page_url)
        all_chunks.extend(chunks)

    if not all_chunks:
        return f"Deep search found pages but could not extract content for: {query}"

    # Step 5: Score and rank
    scored = _score_chunks(query, all_chunks)
    top = scored[:num_chunks]

    # Step 6: Format output
    lines = [f"Deep search results for: {query}\n"]
    for i, chunk in enumerate(top, 1):
        domain = urlparse(chunk["url"]).netloc
        score = chunk.get("score", 0)
        lines.append(f"[{i}] (score: {score:.2f}) from {domain}")
        lines.append(f"   {chunk['url']}")
        lines.append(f"   {chunk['text']}")
        lines.append("")

    return "\n".join(lines).strip()


def register_all():
    """Register web tools."""
    register(ToolDefinition(
        name="web_search",
        description="Search the web using a query. Returns titles, URLs, and snippets for the top results.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "num_results": {
                    "type": "integer",
                    "description": "Number of results to return (default 5, max 10)",
                },
            },
            "required": ["query"],
        },
        handler=_web_search,
        permission="none",
    ))

    register(ToolDefinition(
        name="web_fetch",
        description="Fetch a URL and extract its readable text content. Good for reading articles, documentation, and web pages.",
        parameters={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch and extract content from",
                },
            },
            "required": ["url"],
        },
        handler=_web_fetch,
        permission="none",
    ))

    register(ToolDefinition(
        name="web_search_deep",
        description=(
            "Deep web search: queries SearXNG, fetches the top pages, extracts their "
            "content, and returns the most relevant chunks ranked by query relevance. "
            "Use this when you need detailed, in-depth information rather than just "
            "search snippets. Slower than web_search (~2-3s) but returns actual page "
            "content ready for analysis."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "num_pages": {
                    "type": "integer",
                    "description": "Number of pages to fetch and analyze (default 3)",
                },
                "num_chunks": {
                    "type": "integer",
                    "description": "Number of top-ranked content chunks to return (default 10)",
                },
            },
            "required": ["query"],
        },
        handler=_web_search_deep,
        permission="none",
    ))
