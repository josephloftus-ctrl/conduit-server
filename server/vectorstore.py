"""Firestore vector store — async document storage + KNN search for memories."""

import logging
import os
import time

from google.cloud.firestore import AsyncClient
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.vector import Vector
from google.cloud.firestore_v1.base_vector_query import DistanceMeasure

from . import config

log = logging.getLogger("conduit.vectorstore")

_db: AsyncClient | None = None
_available = False

COLLECTION = "memories"


async def init() -> bool:
    """Create AsyncClient and verify connection. Returns True if successful."""
    global _db, _available
    project = os.getenv("GCP_PROJECT", "")
    if not project:
        log.warning("GCP_PROJECT not set — vectorstore unavailable")
        return False
    try:
        _db = AsyncClient(project=project)
        # Verify connection with a lightweight operation
        _ = _db.collection(COLLECTION)
        _available = True
        log.info("Firestore connected (project=%s, collection=%s)", project, COLLECTION)
        return True
    except Exception as e:
        log.error("Firestore init failed: %s", e)
        _available = False
        return False


async def close():
    """Close the Firestore client."""
    global _db, _available
    if _db:
        _db.close()
        _db = None
        _available = False


def is_available() -> bool:
    """Check if Firestore is reachable."""
    return _available and _db is not None


async def upsert_memory(doc_id: str, category: str, content: str,
                        embedding: list[float], importance: int,
                        source_conversation: str | None = None,
                        created_at: float | None = None):
    """Create or update a memory document."""
    if not _db:
        return
    doc_ref = _db.collection(COLLECTION).document(doc_id)
    await doc_ref.set({
        "category": category,
        "content": content,
        "embedding": Vector(embedding),
        "importance": importance,
        "source_conversation": source_conversation,
        "created_at": created_at or time.time(),
        "last_accessed": None,
        "access_count": 0,
    })

    # Write-through to BM25 index
    from . import memory_index
    memory_index.upsert(doc_id, content, category)


async def vector_search(query_embedding: list[float],
                        top_k: int | None = None) -> list[dict]:
    """KNN vector search. Returns top-K most similar memories."""
    if not _db:
        return []
    top_k = top_k or config.SEARCH_TOP_K
    try:
        collection = _db.collection(COLLECTION)
        query = collection.find_nearest(
            vector_field="embedding",
            query_vector=Vector(query_embedding),
            distance_measure=DistanceMeasure.COSINE,
            limit=top_k,
        )
        docs = await query.get()
        results = []
        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            data.pop("embedding", None)  # Don't return the vector
            results.append(data)
        return results
    except Exception as e:
        log.error("Vector search failed: %s", e)
        return []


async def get_by_id(doc_id: str) -> dict | None:
    """Fetch a single memory by its document ID."""
    if not _db:
        return None
    try:
        doc_ref = _db.collection(COLLECTION).document(doc_id)
        doc = await doc_ref.get()
        if doc.exists:
            data = doc.to_dict()
            data["id"] = doc.id
            data.pop("embedding", None)
            return data
        return None
    except Exception as e:
        log.debug("get_by_id failed for %s: %s", doc_id, e)
        return None


async def get_high_importance(floor: int | None = None,
                              limit: int = 10) -> list[dict]:
    """Fetch memories with importance >= floor, ordered by importance DESC."""
    if not _db:
        return []
    floor = floor or config.IMPORTANCE_FLOOR
    try:
        collection = _db.collection(COLLECTION)
        query = (collection
                 .where(filter=FieldFilter("importance", ">=", floor))
                 .order_by("importance", direction="DESCENDING")
                 .limit(limit))
        docs = await query.get()
        results = []
        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            data.pop("embedding", None)
            results.append(data)
        return results
    except Exception as e:
        log.error("High importance query failed: %s", e)
        return []


async def get_high_importance_recent(floor: int | None = None,
                                     limit: int = 5) -> list[dict]:
    """Fetch high-importance memories, weighted by recency.

    Pulls a larger set of important memories, then scores them by a
    combination of importance and recency so that stale memories don't
    dominate the context window every turn.
    """
    if not _db:
        return []
    floor = floor or config.IMPORTANCE_FLOOR
    try:
        collection = _db.collection(COLLECTION)
        # Fetch more than needed so we can re-rank
        query = (collection
                 .where(filter=FieldFilter("importance", ">=", floor))
                 .order_by("importance", direction="DESCENDING")
                 .limit(limit * 4))
        docs = await query.get()

        now = time.time()
        scored = []
        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            data.pop("embedding", None)

            importance = data.get("importance", 5)
            # Use last_accessed if available, otherwise created_at
            ts = data.get("last_accessed") or data.get("created_at") or 0
            age_days = max((now - ts) / 86400, 0.01) if ts else 30.0

            # Score: importance matters most, but decays with age.
            # A 30-day-old importance-10 memory scores ~5.5
            # A 1-day-old importance-8 memory scores ~8.0
            recency_factor = 1.0 / (1.0 + (age_days / 7.0))
            score = importance * (0.5 + 0.5 * recency_factor)

            scored.append((score, data))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [data for _, data in scored[:limit]]

    except Exception as e:
        log.error("High importance recent query failed: %s", e)
        return []


async def get_all(limit: int = 500) -> list[dict]:
    """Get all memories (for /memories command and settings UI)."""
    if not _db:
        return []
    try:
        collection = _db.collection(COLLECTION)
        query = (collection
                 .order_by("importance", direction="DESCENDING")
                 .limit(limit))
        docs = await query.get()
        results = []
        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            data.pop("embedding", None)
            results.append(data)
        return results
    except Exception as e:
        log.error("Get all memories failed: %s", e)
        return []


async def find_similar(embedding: list[float],
                       threshold: float | None = None) -> dict | None:
    """Find the most similar memory above threshold. Returns it or None."""
    if not _db:
        return None
    threshold = threshold or config.DEDUP_THRESHOLD
    try:
        collection = _db.collection(COLLECTION)
        query = collection.find_nearest(
            vector_field="embedding",
            query_vector=Vector(embedding),
            distance_measure=DistanceMeasure.COSINE,
            limit=1,
            distance_result_field="distance",
        )
        docs = await query.get()
        if not docs:
            return None
        doc = docs[0]
        data = doc.to_dict()
        # Cosine distance: 0 = identical, 2 = opposite
        # Similarity = 1 - distance
        distance = data.pop("distance", 1.0)
        similarity = 1.0 - distance
        if similarity >= threshold:
            data["id"] = doc.id
            data["similarity"] = similarity
            data.pop("embedding", None)
            return data
        return None
    except Exception as e:
        log.error("Find similar failed: %s", e)
        return None


async def touch(doc_id: str):
    """Increment access_count and update last_accessed."""
    if not _db:
        return
    try:
        from google.cloud.firestore_v1 import transforms
        doc_ref = _db.collection(COLLECTION).document(doc_id)
        await doc_ref.update({
            "last_accessed": time.time(),
            "access_count": transforms.Increment(1),
        })
    except Exception as e:
        log.debug("Touch failed for %s: %s", doc_id, e)


async def reinforce(doc_id: str, importance: int):
    """Reinforce a memory — bump access count, refresh timestamp, optionally upgrade importance."""
    if not _db:
        return
    try:
        from google.cloud.firestore_v1 import transforms
        doc_ref = _db.collection(COLLECTION).document(doc_id)
        await doc_ref.update({
            "last_accessed": time.time(),
            "access_count": transforms.Increment(1),
            "importance": importance,
        })
    except Exception as e:
        log.debug("Reinforce failed for %s: %s", doc_id, e)


async def decay(doc_id: str, new_importance: int):
    """Reduce a memory's importance (called during periodic decay)."""
    if not _db:
        return
    try:
        doc_ref = _db.collection(COLLECTION).document(doc_id)
        await doc_ref.update({
            "importance": new_importance,
        })
    except Exception as e:
        log.debug("Decay failed for %s: %s", doc_id, e)


async def update_fields(doc_id: str, fields: dict):
    """Update arbitrary fields on a memory document."""
    if not _db:
        return
    try:
        from google.cloud.firestore_v1.vector import Vector
        # Convert raw embedding lists to Vector objects
        if "embedding" in fields and isinstance(fields["embedding"], list):
            fields["embedding"] = Vector(fields["embedding"])
        doc_ref = _db.collection(COLLECTION).document(doc_id)
        await doc_ref.update(fields)
    except Exception as e:
        log.error("Update fields failed for %s: %s", doc_id, e)


async def get_stale_memories(days: int = 14, limit: int = 50) -> list[dict]:
    """Get memories that haven't been accessed in N days."""
    if not _db:
        return []
    try:
        cutoff = time.time() - (days * 86400)
        collection = _db.collection(COLLECTION)

        # Memories never accessed (last_accessed is None) and old
        results = []

        # Get memories ordered by last_accessed (oldest first)
        # Firestore doesn't support OR queries well, so we do two passes
        # Pass 1: memories with last_accessed < cutoff
        query = (collection
                 .where(filter=FieldFilter("last_accessed", "<", cutoff))
                 .order_by("last_accessed")
                 .limit(limit))
        docs = await query.get()
        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            data.pop("embedding", None)
            results.append(data)

        # Pass 2: memories with last_accessed == None (never accessed)
        # and created_at < cutoff
        if len(results) < limit:
            query2 = (collection
                      .where(filter=FieldFilter("last_accessed", "==", None))
                      .where(filter=FieldFilter("created_at", "<", cutoff))
                      .limit(limit - len(results)))
            docs2 = await query2.get()
            for doc in docs2:
                data = doc.to_dict()
                data["id"] = doc.id
                data.pop("embedding", None)
                results.append(data)

        return results

    except Exception as e:
        log.error("Get stale memories failed: %s", e)
        return []


async def delete(doc_id: str):
    """Delete a memory document."""
    if not _db:
        return
    try:
        doc_ref = _db.collection(COLLECTION).document(doc_id)
        await doc_ref.delete()

        # Delete from BM25 index
        from . import memory_index
        memory_index.delete(doc_id)
    except Exception as e:
        log.error("Delete failed for %s: %s", doc_id, e)


async def count() -> int:
    """Count total memories."""
    if not _db:
        return 0
    try:
        collection = _db.collection(COLLECTION)
        query = collection.count()
        result = await query.get()
        return result[0][0].value if result and result[0] else 0
    except Exception as e:
        log.error("Count failed: %s", e)
        return 0
