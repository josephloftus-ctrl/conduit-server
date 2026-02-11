"""Memory system — extraction, retrieval, summarization for cross-conversation persistence."""

import json
import logging

from . import config, db

log = logging.getLogger("conduit.memory")

# Memory categories
CATEGORIES = {"preference", "fact", "person", "task", "schedule", "topic"}

# Extraction prompt for Haiku
EXTRACTION_PROMPT = """Analyze this conversation exchange and extract any memorable facts worth remembering for future conversations.

User: {user_message}
Assistant: {assistant_message}

Extract memories as a JSON array. Each memory should have:
- "category": one of "preference", "fact", "person", "task", "schedule", "topic"
- "content": a concise statement of the fact (max 100 chars)
- "importance": 1-10 (10 = critical personal info, 1 = trivial)

Categories:
- preference: likes, dislikes, habits (e.g., "Prefers dark roast coffee")
- fact: factual info about the user or their world (e.g., "Works at Lockheed Martin Building 100")
- person: info about people mentioned (e.g., "Ken is his boss, takes data seriously")
- task: ongoing tasks or projects (e.g., "Working on inventory system migration")
- schedule: schedule-related info (e.g., "Has team meeting every Monday at 9am")
- topic: topics of interest (e.g., "Interested in AI-powered operations")

Rules:
- Only extract genuinely useful, long-term facts
- Skip ephemeral conversational filler
- Skip facts that are only relevant to this conversation
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


async def extract_memories(user_message: str, assistant_message: str,
                           conversation_id: str):
    """Extract memories from a conversation exchange using Haiku (background task)."""
    if not config.EXTRACTION_ENABLED:
        return

    # Get the brain provider
    from .app import providers
    brain = providers.get(config.BRAIN_PROVIDER)
    if not brain:
        log.debug("Brain provider not available for memory extraction")
        return

    prompt = EXTRACTION_PROMPT.format(
        user_message=user_message[:1000],
        assistant_message=assistant_message[:1000],
    )

    try:
        response, usage = await brain.generate(
            [{"role": "user", "content": prompt}],
            system="You extract memories from conversations. Reply with only valid JSON arrays.",
        )
        await db.log_usage(brain.name, brain.model, usage.input_tokens, usage.output_tokens)

        # Parse JSON response
        text = response.strip()
        # Handle markdown code blocks
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0]
        text = text.strip()

        memories = json.loads(text)
        if not isinstance(memories, list):
            return

        # Store each memory
        count = await db.count_memories()
        for mem in memories[:3]:  # cap at 3 per exchange
            category = mem.get("category", "fact")
            content = mem.get("content", "").strip()
            importance = min(max(int(mem.get("importance", 5)), 1), 10)

            if not content or category not in CATEGORIES:
                continue

            # Skip duplicates
            if await db.find_duplicate_memory(content):
                continue

            # Enforce max memories — drop least important if at cap
            if count >= config.MAX_MEMORIES:
                await _evict_least_important()

            await db.add_memory(
                category=category,
                content=content,
                source_conversation=conversation_id,
                importance=importance,
            )
            count += 1
            log.info("Memory stored: [%s] %s (importance: %d)", category, content, importance)

    except json.JSONDecodeError:
        log.debug("Memory extraction returned non-JSON: %s", response[:100] if response else "empty")
    except Exception as e:
        log.error("Memory extraction failed: %s", e)


async def get_memory_context() -> str:
    """Build formatted memory context string for system prompt injection."""
    memories = await db.get_memories(limit=50)
    if not memories:
        return ""

    # Group by category
    grouped: dict[str, list[str]] = {}
    for m in memories:
        cat = m["category"]
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
            for item in items[:10]:  # cap per category
                lines.append(f"- {item}")

    return "\n".join(lines)


async def get_all_memories() -> list[dict]:
    """Get all memories (for /memories command and settings UI)."""
    return await db.get_memories(limit=config.MAX_MEMORIES)


async def summarize_conversation(conversation_id: str):
    """Summarize a conversation using NIM (free). Background task."""
    from .app import providers, get_provider

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
    """Build conversation context: summaries of old messages + recent messages.

    Returns messages list suitable for sending to a provider.
    """
    summaries = await db.get_conversation_summaries(conversation_id)
    messages = await db.get_messages(conversation_id, limit=100)

    result = []

    # Add summaries as context
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

    # Add recent messages (after last summary)
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


async def _evict_least_important():
    """Remove the least important, least accessed memory to make room."""
    import aiosqlite
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await conn.execute_fetchall(
            "SELECT id FROM memories ORDER BY importance ASC, access_count ASC, created_at ASC LIMIT 1"
        )
        if rows:
            await conn.execute("DELETE FROM memories WHERE id = ?", (rows[0]["id"],))
            await conn.commit()
            log.info("Evicted memory %s to make room", rows[0]["id"])
