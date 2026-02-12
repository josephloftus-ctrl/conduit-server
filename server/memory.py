"""Memory system — semantic extraction, retrieval, and summarization.

Uses Gemini embeddings + Firestore vector search for semantic memory.
SQLite still handles conversations, summaries, usage, and KV.
"""

import asyncio
import json
import logging
import uuid

from . import config, db, embeddings, vectorstore

log = logging.getLogger("conduit.memory")

# Memory categories
CATEGORIES = {"preference", "fact", "person", "task", "schedule", "topic"}

# Extraction prompt — now includes existing memories for contradiction detection
EXTRACTION_PROMPT = """Analyze this conversation exchange and extract any memorable facts worth remembering for future conversations.

User: {user_message}
Assistant: {assistant_message}

{existing_context}

Extract memories as a JSON array. Each memory should have:
- "category": one of "preference", "fact", "person", "task", "schedule", "topic"
- "content": a concise statement of the fact (max 100 chars)
- "importance": 1-10 (10 = critical personal info, 1 = trivial)
- "action": one of "create", "update", "supersede"

Categories:
- preference: likes, dislikes, habits (e.g., "Prefers dark roast coffee")
- fact: factual info about the user or their world (e.g., "Works at Lockheed Martin Building 100")
- person: info about people mentioned (e.g., "Ken is his boss, takes data seriously")
- task: ongoing tasks or projects (e.g., "Working on inventory system migration")
- schedule: schedule-related info (e.g., "Has team meeting every Monday at 9am")
- topic: topics of interest (e.g., "Interested in AI-powered operations")

Actions:
- create: brand new fact not covered by existing memories
- update: refines or adds detail to an existing memory (include "updates_id" field with the memory ID)
- supersede: replaces an existing memory that is now outdated/wrong (include "supersedes_id" field with the memory ID)

Rules:
- Only extract genuinely useful, long-term facts
- Skip ephemeral conversational filler
- Skip facts that are only relevant to this conversation
- Direct statements ("I'm allergic to shellfish") deserve importance 8-10
- Inferred or tangential mentions deserve importance 4-6
- If new info contradicts an existing memory, use "supersede" to replace it
- If nothing worth remembering, return an empty array: []
- Max 3 memories per exchange

Reply with ONLY the JSON array, no other text."""

SUMMARY_PROMPT = """Summarize this conversation concisely. Focus on:
- Key topics discussed
- Decisions made
- Action items or follow-ups
- Any important context for future reference

Keep it under 150 words.

Conversation:
{messages}"""

CONSOLIDATION_PROMPT = """Review these memories stored about a user. Identify issues and suggest cleanup actions.

Memories:
{memories}

Return a JSON array of actions. Each action should be one of:
- {{"action": "merge", "ids": ["id1", "id2"], "merged_content": "combined statement", "category": "...", "importance": N}}
  Use when two memories say essentially the same thing.
- {{"action": "delete", "id": "...", "reason": "..."}}
  Use when a memory is stale, trivial, or superseded by another.
- {{"action": "update", "id": "...", "content": "improved statement", "importance": N}}
  Use when a memory's wording is unclear or importance seems wrong.

Rules:
- Only suggest changes that clearly improve quality
- Prefer merging near-duplicates over keeping both
- Delete memories about completed tasks or outdated facts
- If everything looks good, return an empty array: []
- Max 10 actions per review

Reply with ONLY the JSON array, no other text."""


async def extract_memories(user_message: str, assistant_message: str,
                           conversation_id: str):
    """Extract memories from a conversation exchange using Haiku, then embed and store."""
    if not config.EXTRACTION_ENABLED:
        return

    # Get the brain provider
    from .app import providers
    brain = providers.get(config.BRAIN_PROVIDER)
    if not brain:
        log.debug("Brain provider not available for memory extraction")
        return

    # Fetch existing memories for contradiction detection
    existing_context = ""
    if vectorstore.is_available():
        try:
            query_embedding = await embeddings.embed_text(user_message[:500])
            similar = await vectorstore.vector_search(query_embedding, top_k=5)
            if similar:
                lines = ["Existing memories (check for contradictions or updates):"]
                for m in similar:
                    lines.append(f'- [id:{m["id"]}] [{m.get("category", "fact")}] {m["content"]}')
                existing_context = "\n".join(lines)
        except Exception as e:
            log.debug("Failed to fetch existing memories for extraction: %s", e)

    prompt = EXTRACTION_PROMPT.format(
        user_message=user_message[:1000],
        assistant_message=assistant_message[:1000],
        existing_context=existing_context,
    )

    try:
        response, usage = await brain.generate(
            [{"role": "user", "content": prompt}],
            system="You extract memories from conversations. Reply with only valid JSON arrays.",
        )
        await db.log_usage(brain.name, brain.model, usage.input_tokens, usage.output_tokens)

        # Parse JSON response
        text = response.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        memories = json.loads(text)
        if not isinstance(memories, list):
            return

        if not vectorstore.is_available():
            log.debug("Vectorstore unavailable — skipping memory storage")
            return

        for mem in memories[:3]:
            category = mem.get("category", "fact")
            content = mem.get("content", "").strip()
            importance = min(max(int(mem.get("importance", 5)), 1), 10)
            action = mem.get("action", "create")

            if not content or category not in CATEGORIES:
                continue

            try:
                # Handle supersede — delete the old memory, store new one
                if action == "supersede":
                    old_id = mem.get("supersedes_id", "")
                    if old_id:
                        await vectorstore.delete(old_id)
                        log.info("Memory superseded: %s → %s", old_id, content[:50])

                # Handle update — refresh an existing memory
                if action == "update":
                    update_id = mem.get("updates_id", "")
                    if update_id:
                        embedding = await embeddings.embed_text(content)
                        await vectorstore.upsert_memory(
                            doc_id=update_id,
                            category=category,
                            content=content,
                            embedding=embedding,
                            importance=importance,
                            source_conversation=conversation_id,
                        )
                        log.info("Memory updated: [%s] %s (importance: %d)", category, content, importance)
                        continue

                # Embed the memory content
                embedding = await embeddings.embed_text(content)

                # Semantic dedup check
                existing = await vectorstore.find_similar(embedding, config.DEDUP_THRESHOLD)
                if existing:
                    # Reinforce — bump access count and refresh timestamp,
                    # upgrade importance if new mention is higher
                    new_importance = max(importance, existing.get("importance", 0))
                    await vectorstore.reinforce(existing["id"], new_importance)
                    log.info("Memory reinforced: %s (importance: %d, accesses: +1)",
                             existing["content"][:50], new_importance)
                    continue

                # New memory — store it
                doc_id = uuid.uuid4().hex[:12]
                await vectorstore.upsert_memory(
                    doc_id=doc_id,
                    category=category,
                    content=content,
                    embedding=embedding,
                    importance=importance,
                    source_conversation=conversation_id,
                )
                log.info("Memory stored: [%s] %s (importance: %d)", category, content, importance)

            except Exception as e:
                log.warning("Failed to embed/store memory '%s': %s", content[:50], e)

    except json.JSONDecodeError:
        log.debug("Memory extraction returned non-JSON: %s", response[:100] if response else "empty")
    except Exception as e:
        log.error("Memory extraction failed: %s", e)


async def get_memory_context(query: str = "") -> str:
    """Build formatted memory context for system prompt injection.

    Uses semantic search to find query-relevant memories. Only falls back
    to high-importance memories when semantic search returns too few results,
    and even then applies recency weighting to avoid surfacing stale memories.
    """
    if not vectorstore.is_available():
        return ""

    try:
        memories_by_id: dict[str, dict] = {}
        semantic_count = 0

        if query:
            # Semantic search for query-relevant memories
            try:
                query_embedding = await embeddings.embed_query(query)
                results = await vectorstore.vector_search(query_embedding, config.SEARCH_TOP_K)
                for m in results:
                    memories_by_id[m["id"]] = m
                semantic_count = len(memories_by_id)
            except Exception as e:
                log.warning("Semantic search failed: %s", e)

        # Only fall back to high-importance memories if semantic search
        # returned fewer than 3 relevant results (avoids injecting stale
        # memories when we already have good context)
        if semantic_count < 3:
            high = await vectorstore.get_high_importance_recent(
                config.IMPORTANCE_FLOOR, limit=5,
            )
            for m in high:
                memories_by_id.setdefault(m["id"], m)

        if not memories_by_id:
            return ""

        # Touch accessed memories in the background (fire-and-forget)
        for doc_id in memories_by_id:
            asyncio.create_task(vectorstore.touch(doc_id))

        # Group by category
        grouped: dict[str, list[str]] = {}
        for m in memories_by_id.values():
            cat = m.get("category", "fact")
            if cat not in grouped:
                grouped[cat] = []
            grouped[cat].append(m["content"])

        lines = ["Things I remember about you:"]
        category_labels = {
            "preference": "Preferences",
            "fact": "Facts",
            "person": "People",
            "task": "Tasks",
            "schedule": "Schedule",
            "topic": "Interests",
        }

        for cat, label in category_labels.items():
            items = grouped.get(cat, [])
            if items:
                lines.append(f"\n{label}:")
                for item in items[:10]:
                    lines.append(f"- {item}")

        return "\n".join(lines)

    except Exception as e:
        log.warning("Failed to get memory context: %s", e)
        return ""


async def get_all_memories() -> list[dict]:
    """Get all memories (for /memories command and settings UI)."""
    if not vectorstore.is_available():
        return []
    return await vectorstore.get_all(limit=config.MAX_MEMORIES)


async def decay_memories():
    """Reduce importance of memories that haven't been accessed recently.

    Called periodically by the scheduler. Memories that haven't been
    touched in 14+ days lose 1 importance point. Memories that drop
    below importance 3 are deleted (forgotten).
    """
    if not vectorstore.is_available():
        return

    try:
        stale = await vectorstore.get_stale_memories(days=14, limit=50)
        decayed = 0
        deleted = 0

        for mem in stale:
            current_importance = mem.get("importance", 5)
            new_importance = current_importance - 1

            if new_importance < 3:
                await vectorstore.delete(mem["id"])
                deleted += 1
                log.debug("Memory forgotten (decayed below 3): %s", mem["content"][:50])
            else:
                await vectorstore.decay(mem["id"], new_importance)
                decayed += 1

        if decayed or deleted:
            log.info("Memory decay: %d decayed, %d forgotten", decayed, deleted)

    except Exception as e:
        log.error("Memory decay failed: %s", e)


async def consolidate_memories():
    """Review all memories and merge/prune using a cheap model.

    Called weekly by the scheduler. Uses the brain provider (Haiku) to
    identify duplicates, stale entries, and unclear wording.
    """
    if not vectorstore.is_available():
        return

    from .app import providers
    brain = providers.get(config.BRAIN_PROVIDER)
    if not brain:
        log.debug("Brain provider not available for consolidation")
        return

    try:
        all_memories = await vectorstore.get_all(limit=100)
        if len(all_memories) < 5:
            return  # Not enough to consolidate

        # Format memories for the prompt
        lines = []
        for m in all_memories:
            age_days = 0
            if m.get("created_at"):
                import time
                age_days = int((time.time() - m["created_at"]) / 86400)
            accesses = m.get("access_count", 0)
            lines.append(
                f'[id:{m["id"]}] [{m.get("category", "fact")}] '
                f'importance:{m.get("importance", 5)} '
                f'age:{age_days}d accesses:{accesses} '
                f'"{m["content"]}"'
            )

        prompt = CONSOLIDATION_PROMPT.format(memories="\n".join(lines))
        response, usage = await brain.generate(
            [{"role": "user", "content": prompt}],
            system="You review and clean up memory stores. Reply with only valid JSON arrays.",
        )
        await db.log_usage(brain.name, brain.model, usage.input_tokens, usage.output_tokens)

        # Parse response
        text = response.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        actions = json.loads(text)
        if not isinstance(actions, list):
            return

        applied = 0
        for act in actions[:10]:
            try:
                action_type = act.get("action")

                if action_type == "delete":
                    doc_id = act.get("id", "")
                    if doc_id:
                        await vectorstore.delete(doc_id)
                        log.info("Consolidation delete: %s (%s)", doc_id, act.get("reason", ""))
                        applied += 1

                elif action_type == "merge":
                    ids = act.get("ids", [])
                    merged_content = act.get("merged_content", "")
                    category = act.get("category", "fact")
                    importance = act.get("importance", 5)
                    if len(ids) >= 2 and merged_content:
                        # Delete old entries
                        for old_id in ids:
                            await vectorstore.delete(old_id)
                        # Create merged entry
                        embedding = await embeddings.embed_text(merged_content)
                        doc_id = uuid.uuid4().hex[:12]
                        await vectorstore.upsert_memory(
                            doc_id=doc_id,
                            category=category,
                            content=merged_content,
                            embedding=embedding,
                            importance=importance,
                        )
                        log.info("Consolidation merge: %s → %s", ids, merged_content[:50])
                        applied += 1

                elif action_type == "update":
                    doc_id = act.get("id", "")
                    new_content = act.get("content", "")
                    importance = act.get("importance")
                    if doc_id and new_content:
                        embedding = await embeddings.embed_text(new_content)
                        update_fields = {
                            "content": new_content,
                            "embedding": embedding,
                        }
                        if importance is not None:
                            update_fields["importance"] = importance
                        await vectorstore.update_fields(doc_id, update_fields)
                        log.info("Consolidation update: %s → %s", doc_id, new_content[:50])
                        applied += 1

            except Exception as e:
                log.warning("Consolidation action failed: %s", e)

        log.info("Memory consolidation complete: %d actions applied", applied)

    except json.JSONDecodeError:
        log.debug("Consolidation returned non-JSON")
    except Exception as e:
        log.error("Memory consolidation failed: %s", e)


async def summarize_conversation(conversation_id: str):
    """Summarize a conversation using NIM (free). Background task."""
    from .app import get_provider

    messages = await db.get_messages(conversation_id, limit=100)
    if len(messages) < config.SUMMARY_THRESHOLD:
        return

    # Check if we already have a summary for this range
    existing = await db.get_conversation_summaries(conversation_id)
    last_summarized = 0
    if existing:
        last_range = existing[-1].get("message_range", "")
        if "-" in last_range:
            try:
                last_summarized = int(last_range.split("-")[1])
            except (ValueError, IndexError):
                pass

    # Only summarize new messages
    new_messages = messages[last_summarized:]
    if len(new_messages) < 10:
        return

    # Format messages for summary
    formatted = []
    for m in new_messages:
        role = "User" if m["role"] == "user" else "Assistant"
        formatted.append(f"{role}: {m['content'][:200]}")

    prompt = SUMMARY_PROMPT.format(messages="\n".join(formatted))

    # Use default provider (NIM — free)
    provider = get_provider()
    try:
        response, usage = await provider.generate(
            [{"role": "user", "content": prompt}],
            system="You summarize conversations concisely.",
        )
        await db.log_usage(provider.name, provider.model, usage.input_tokens, usage.output_tokens)

        message_range = f"{last_summarized}-{len(messages)}"
        await db.add_conversation_summary(conversation_id, response.strip(), message_range)
        log.info("Conversation %s summarized (messages %s)", conversation_id, message_range)

    except Exception as e:
        log.error("Conversation summarization failed: %s", e)


async def get_conversation_context(conversation_id: str) -> list[dict]:
    """Build conversation context: summaries of old messages + recent messages."""
    summaries = await db.get_conversation_summaries(conversation_id)
    messages = await db.get_messages(conversation_id, limit=100)

    result = []

    if summaries:
        summary_text = "\n\n".join(
            f"[Earlier in conversation] {s['summary']}" for s in summaries
        )
        result.append({
            "role": "user",
            "content": f"[Context from earlier in our conversation:\n{summary_text}]",
        })
        result.append({
            "role": "assistant",
            "content": "I remember our earlier discussion. How can I help?",
        })

    last_summarized = 0
    if summaries:
        last_range = summaries[-1].get("message_range", "")
        if "-" in last_range:
            try:
                last_summarized = int(last_range.split("-")[1])
            except (ValueError, IndexError):
                pass

    recent = messages[last_summarized:]
    for m in recent:
        result.append({"role": m["role"], "content": m["content"]})

    return result
