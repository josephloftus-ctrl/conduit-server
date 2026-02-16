"""SQLite FTS5 memory index — BM25 keyword search complement to vector search.

Provides a local keyword index that runs alongside Firestore vector search.
Write-through: vectorstore calls upsert/delete here on every mutation.
Startup: sync_from_firestore() rebuilds the full index from Firestore.
"""

import logging
import os
import sqlite3

from . import config

log = logging.getLogger("conduit.memory_index")

_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection | None:
    """Lazy-init SQLite connection with WAL mode and FTS5 table."""
    global _conn
    if _conn is not None:
        return _conn

    if not config.BM25_ENABLED:
        return None

    try:
        db_path = os.path.expanduser(config.BM25_DB_PATH)
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        _conn = sqlite3.connect(db_path, check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts "
            "USING fts5(doc_id, content, category, tokenize='porter unicode61')"
        )
        _conn.commit()
        log.info("BM25 index opened: %s", db_path)
        return _conn
    except Exception as e:
        log.error("Failed to open BM25 index: %s", e)
        _conn = None
        return None


def upsert(doc_id: str, content: str, category: str) -> None:
    """Insert or replace a document in the FTS5 index."""
    conn = _get_conn()
    if not conn:
        return
    try:
        # Delete-then-insert (FTS5 doesn't support UPDATE)
        conn.execute("DELETE FROM memory_fts WHERE doc_id = ?", (doc_id,))
        conn.execute(
            "INSERT INTO memory_fts (doc_id, content, category) VALUES (?, ?, ?)",
            (doc_id, content, category),
        )
        conn.commit()
    except Exception as e:
        log.warning("BM25 upsert failed for %s: %s", doc_id, e)


def delete(doc_id: str) -> None:
    """Remove a document from the FTS5 index."""
    conn = _get_conn()
    if not conn:
        return
    try:
        conn.execute("DELETE FROM memory_fts WHERE doc_id = ?", (doc_id,))
        conn.commit()
    except Exception as e:
        log.warning("BM25 delete failed for %s: %s", doc_id, e)


def search(query: str, top_k: int | None = None) -> list[dict]:
    """BM25-ranked keyword search. Returns [{doc_id, content, category, score}]."""
    conn = _get_conn()
    if not conn:
        return []
    top_k = top_k or config.HYBRID_TOP_K

    try:
        # Wrap query in quotes for phrase matching, escape internal quotes
        safe_query = '"' + query.replace('"', '""') + '"'
        cursor = conn.execute(
            "SELECT doc_id, content, category, rank "
            "FROM memory_fts "
            "WHERE memory_fts MATCH ? "
            "ORDER BY rank "
            "LIMIT ?",
            (safe_query, top_k),
        )
        results = []
        for row in cursor:
            results.append({
                "doc_id": row[0],
                "content": row[1],
                "category": row[2],
                "score": row[3],
            })
        return results
    except Exception as e:
        log.debug("BM25 search failed: %s", e)
        return []


async def sync_from_firestore() -> None:
    """Full rebuild of the FTS5 index from Firestore via vectorstore.get_all()."""
    conn = _get_conn()
    if not conn:
        return

    try:
        from . import vectorstore

        all_memories = await vectorstore.get_all(limit=1000)
        log.info("BM25 sync: fetched %d memories from Firestore", len(all_memories))

        # Clear existing index
        conn.execute("DELETE FROM memory_fts")

        # Re-insert all
        for mem in all_memories:
            doc_id = mem.get("id", "")
            content = mem.get("content", "")
            category = mem.get("category", "fact")
            if doc_id and content:
                conn.execute(
                    "INSERT INTO memory_fts (doc_id, content, category) VALUES (?, ?, ?)",
                    (doc_id, content, category),
                )

        conn.commit()
        log.info("BM25 sync complete: %d documents indexed", len(all_memories))

    except Exception as e:
        log.error("BM25 sync from Firestore failed: %s", e)


def close() -> None:
    """Close the SQLite connection."""
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None
        log.info("BM25 index closed")
