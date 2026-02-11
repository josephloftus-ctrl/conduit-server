"""SQLite database via aiosqlite — schema + helpers."""

import json
import time
import uuid
from datetime import datetime
from pathlib import Path

import aiosqlite

DB_PATH = Path(__file__).parent / "conduit.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New Chat',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    model TEXT,
    source TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    cron TEXT NOT NULL,
    prompt TEXT NOT NULL,
    model_tier INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run REAL
);

CREATE TABLE IF NOT EXISTS model_usage (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL DEFAULT 'fact',
    content TEXT NOT NULL,
    source_conversation TEXT,
    importance INTEGER NOT NULL DEFAULT 5,
    created_at REAL NOT NULL,
    last_accessed REAL,
    access_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance DESC);

CREATE TABLE IF NOT EXISTS conversation_summaries (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    message_range TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_summaries_conv ON conversation_summaries(conversation_id);
"""


async def init_db():
    """Create tables if they don't exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


def _now() -> float:
    return time.time()


def _id() -> str:
    return uuid.uuid4().hex[:12]


# --- Conversations ---

async def create_conversation(title: str = "New Chat") -> str:
    cid = _id()
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (cid, title, now, now),
        )
        await db.commit()
    return cid


async def list_conversations(limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]


async def update_conversation_title(cid: str, title: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now(), cid),
        )
        await db.commit()


# --- Messages ---

async def add_message(conversation_id: str, role: str, content: str,
                      model: str | None = None, source: str | None = None) -> str:
    mid = _id()
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO messages (id, conversation_id, role, content, model, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (mid, conversation_id, role, content, model, source, now),
        )
        await db.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id)
        )
        await db.commit()
    return mid


async def get_messages(conversation_id: str, limit: int = 100) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at LIMIT ?",
            (conversation_id, limit),
        )
        return [dict(r) for r in rows]


async def get_message_count(conversation_id: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        row = await db.execute_fetchall(
            "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        return row[0][0] if row else 0


# --- Model Usage ---

async def log_usage(provider: str, model: str, input_tokens: int, output_tokens: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO model_usage (id, provider, model, input_tokens, output_tokens, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (_id(), provider, model, input_tokens, output_tokens, _now()),
        )
        await db.commit()


async def get_daily_opus_tokens() -> int:
    """Sum today's Opus output tokens."""
    today_start = datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0
    ).timestamp()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await db.execute_fetchall(
            "SELECT COALESCE(SUM(output_tokens), 0) FROM model_usage "
            "WHERE provider = 'opus' AND created_at >= ?",
            (today_start,),
        )
        return row[0][0] if row else 0


async def get_daily_provider_tokens(provider: str) -> int:
    """Sum today's output tokens for a specific provider."""
    today_start = datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0
    ).timestamp()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await db.execute_fetchall(
            "SELECT COALESCE(SUM(output_tokens), 0) FROM model_usage "
            "WHERE provider = ? AND created_at >= ?",
            (provider, today_start),
        )
        return row[0][0] if row else 0


async def get_usage_by_provider(days: int = 7) -> list[dict]:
    """Get token usage grouped by provider for the last N days."""
    cutoff = _now() - (days * 86400)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT provider, model, "
            "SUM(input_tokens) as total_input, SUM(output_tokens) as total_output, "
            "COUNT(*) as request_count "
            "FROM model_usage WHERE created_at >= ? "
            "GROUP BY provider ORDER BY total_output DESC",
            (cutoff,),
        )
        return [dict(r) for r in rows]


# --- Scheduled Tasks ---

async def get_scheduled_tasks() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM scheduled_tasks WHERE enabled = 1"
        )
        return [dict(r) for r in rows]


async def add_scheduled_task(name: str, cron: str, prompt: str,
                             model_tier: int = 1) -> str:
    tid = _id()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO scheduled_tasks (id, name, cron, prompt, model_tier, enabled) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (tid, name, cron, prompt, model_tier),
        )
        await db.commit()
    return tid


async def update_task_last_run(task_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE scheduled_tasks SET last_run = ? WHERE id = ?",
            (_now(), task_id),
        )
        await db.commit()


async def delete_scheduled_task(task_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
        await db.commit()


# --- KV Store ---

async def kv_get(key: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        row = await db.execute_fetchall(
            "SELECT value FROM kv WHERE key = ?", (key,)
        )
        return row[0][0] if row else None


async def kv_set(key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO kv (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, value, _now()),
        )
        await db.commit()


# --- Memories ---

async def add_memory(category: str, content: str, source_conversation: str | None = None,
                     importance: int = 5) -> str:
    mid = _id()
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO memories (id, category, content, source_conversation, importance, created_at, access_count) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            (mid, category, content, source_conversation, importance, now),
        )
        await db.commit()
    return mid


async def get_memories(limit: int = 200) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM memories ORDER BY importance DESC, created_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]


async def get_memories_by_category(category: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM memories WHERE category = ? ORDER BY importance DESC",
            (category,),
        )
        return [dict(r) for r in rows]


async def delete_memory(memory_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        await db.commit()


async def touch_memory(memory_id: str):
    """Update last_accessed and increment access_count."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE memories SET last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
            (_now(), memory_id),
        )
        await db.commit()


async def count_memories() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        row = await db.execute_fetchall("SELECT COUNT(*) FROM memories")
        return row[0][0] if row else 0


async def find_duplicate_memory(content: str) -> bool:
    """Check if a similar memory already exists (exact content match)."""
    async with aiosqlite.connect(DB_PATH) as db:
        row = await db.execute_fetchall(
            "SELECT COUNT(*) FROM memories WHERE content = ?", (content,)
        )
        return (row[0][0] if row else 0) > 0


# --- Conversation Summaries ---

async def add_conversation_summary(conversation_id: str, summary: str, message_range: str) -> str:
    sid = _id()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO conversation_summaries (id, conversation_id, summary, message_range, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (sid, conversation_id, summary, message_range, _now()),
        )
        await db.commit()
    return sid


async def get_conversation_summaries(conversation_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT * FROM conversation_summaries WHERE conversation_id = ? ORDER BY created_at",
            (conversation_id,),
        )
        return [dict(r) for r in rows]


async def get_recent_conversations_with_summaries(limit: int = 5) -> list[dict]:
    """Get recent conversations with their summaries (for heartbeat context)."""
    convs = await list_conversations(limit=limit)
    result = []
    for c in convs:
        summaries = await get_conversation_summaries(c["id"])
        if summaries:
            result.append({
                "title": c["title"],
                "summary": summaries[-1]["summary"] if summaries else "",
                "updated_at": c["updated_at"],
            })
    return result
