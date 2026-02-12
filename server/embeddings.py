"""Gemini embedding wrapper — async text-embedding-005 via Vertex AI."""

import logging
import os

from google import genai

from . import config

log = logging.getLogger("conduit.embeddings")

_client: genai.Client | None = None


def init():
    """Create a Gemini client for embedding operations. Uses ADC via Vertex AI."""
    global _client
    project = os.getenv("GCP_PROJECT", "")
    if not project:
        log.warning("GCP_PROJECT not set — embeddings unavailable")
        return
    _client = genai.Client(
        vertexai=True,
        project=project,
        location="us-east4",
    )
    log.info("Embedding client initialized (model=%s, dims=%d)",
             config.EMBEDDING_MODEL, config.EMBEDDING_DIMENSIONS)


async def embed_text(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:
    """Embed a single text string. Returns 768-dim vector."""
    if not _client:
        raise RuntimeError("Embedding client not initialized")
    result = await _client.aio.models.embed_content(
        model=config.EMBEDDING_MODEL,
        contents=text,
        config={"task_type": task_type, "output_dimensionality": config.EMBEDDING_DIMENSIONS},
    )
    return list(result.embeddings[0].values)


async def embed_batch(texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    """Embed multiple texts. Returns list of 768-dim vectors."""
    if not _client:
        raise RuntimeError("Embedding client not initialized")
    results = []
    # Process in chunks of 20 (API limit)
    for i in range(0, len(texts), 20):
        chunk = texts[i:i + 20]
        result = await _client.aio.models.embed_content(
            model=config.EMBEDDING_MODEL,
            contents=chunk,
            config={"task_type": task_type, "output_dimensionality": config.EMBEDDING_DIMENSIONS},
        )
        results.extend(list(e.values) for e in result.embeddings)
    return results


async def embed_query(text: str) -> list[float]:
    """Embed a query string for retrieval. Uses RETRIEVAL_QUERY task type."""
    return await embed_text(text, task_type="RETRIEVAL_QUERY")
