#!/usr/bin/env python3
"""One-time migration: SQLite memories → Firestore with embeddings.

Reads existing memories from SQLite, embeds them via Gemini, and stores
in Firestore with semantic dedup checking.
"""

import asyncio
import sys
sys.path.insert(0, ".")

from server import db, embeddings, vectorstore


async def main():
    # Initialize all systems
    await db.init_db()
    embeddings.init()
    vs_ok = await vectorstore.init()
    if not vs_ok:
        print("ERROR: Firestore initialization failed. Check GCP_PROJECT and credentials.")
        sys.exit(1)

    # Read all SQLite memories
    sqlite_memories = await db.get_memories_legacy(limit=500)
    if not sqlite_memories:
        print("No memories found in SQLite. Nothing to migrate.")
        return

    print(f"Found {len(sqlite_memories)} memories in SQLite")
    firestore_count = await vectorstore.count()
    print(f"Existing memories in Firestore: {firestore_count}")

    # Batch embed in chunks of 20
    contents = [m["content"] for m in sqlite_memories]
    print(f"Embedding {len(contents)} memories...")
    all_embeddings = await embeddings.embed_batch(contents)
    print(f"Embeddings generated: {len(all_embeddings)}")

    added = 0
    skipped = 0
    errors = 0

    for i, mem in enumerate(sqlite_memories):
        embedding = all_embeddings[i]

        try:
            # Semantic dedup check against existing Firestore memories
            existing = await vectorstore.find_similar(embedding, threshold=0.9)
            if existing:
                skipped += 1
                continue

            # Use the original SQLite ID as the Firestore doc ID
            await vectorstore.upsert_memory(
                doc_id=mem["id"],
                category=mem["category"],
                content=mem["content"],
                embedding=embedding,
                importance=mem["importance"],
                source_conversation=mem.get("source_conversation"),
                created_at=mem.get("created_at"),
            )
            added += 1

        except Exception as e:
            errors += 1
            print(f"  ERROR migrating '{mem['content'][:50]}': {e}")

    final_count = await vectorstore.count()
    print(f"\nMigration complete:")
    print(f"  SQLite source: {len(sqlite_memories)}")
    print(f"  Added to Firestore: {added}")
    print(f"  Skipped (duplicates): {skipped}")
    print(f"  Errors: {errors}")
    print(f"  Firestore total: {final_count}")
    await vectorstore.close()


if __name__ == "__main__":
    asyncio.run(main())
