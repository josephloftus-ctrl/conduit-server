# Scout-Driven Improvements — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement three improvements sourced from Reddit Scout: GLM-5 as primary provider via NIM, pre-computed project indexes for faster agent navigation, and BM25 hybrid memory retrieval.

**Architecture:** Feature 1 is config-only. Feature 2 adds an indexer module + scheduler job + filesystem tool. Feature 3 adds a SQLite FTS5 mirror alongside existing Firestore vectorstore with Reciprocal Rank Fusion scoring.

**Tech Stack:** Python (FastAPI, SQLite, aiosqlite), YAML config, APScheduler

---

### Task 1: Switch routing to GLM-5 on NIM with MiniMax fallback

**Files:**
- Modify: `server/config.yaml:27-32` (nim provider)
- Modify: `server/config.yaml:88-90` (routing section)

**Step 1: Update NIM provider default model**

In `server/config.yaml`, change line 32 from:
```yaml
      default_model: "moonshotai/kimi-k2.5"
```
to:
```yaml
      default_model: "z-ai/glm-5"
```

**Step 2: Update routing default and fallback chain**

In `server/config.yaml`, change lines 88-90 from:
```yaml
  routing:
    default: "openrouter"
    fallback_chain: ["openrouter", "deepseek-free", "nim", "minimax", "ollama"]
```
to:
```yaml
  routing:
    default: "nim"
    fallback_chain: ["nim", "minimax", "openrouter", "deepseek-free", "ollama"]
```

**Step 3: Verify config loads**

Run: `cd /home/joseph/Projects/conduit && python -c "from server import config; print('default:', config.DEFAULT_PROVIDER); print('chain:', config.FALLBACK_CHAIN)"`
Expected: `default: nim` and `fallback_chain: ['nim', 'minimax', 'openrouter', 'deepseek-free', 'ollama']`

**Step 4: Commit**

```bash
cd /home/joseph/Projects/conduit
git add server/config.yaml
git commit -m "feat: switch to GLM-5 on NIM as primary, MiniMax as first fallback"
```

---

### Task 2: Add indexer config and config loading

**Files:**
- Modify: `server/config.yaml:157` (add indexer section after memory)
- Modify: `server/config.py:54-55` (load indexer config)
- Modify: `server/config.py:163,214-215` (reload indexer config)

**Step 1: Add indexer section to config.yaml**

Insert after line 156 (end of memory section), before line 158 (`scheduler:`):

```yaml
indexer:
  enabled: true
  output_dir: "~/conduit-data/indexes"
  projects:
    - name: spectre
      path: "~/Projects/spectre"
    - name: conduit
      path: "~/conduit"
```

**Step 2: Add config loading in config.py**

After line 54 (`DEDUP_THRESHOLD = ...`), add:

```python
# Indexer
indexer_cfg = _raw.get("indexer", {})
INDEXER_ENABLED = indexer_cfg.get("enabled", False)
INDEXER_OUTPUT_DIR = indexer_cfg.get("output_dir", "~/conduit-data/indexes")
INDEXER_PROJECTS = indexer_cfg.get("projects", [])
```

**Step 3: Add to reload() globals and reload body**

In `config.py` line 163, add to the global declaration:
```python
    global INDEXER_ENABLED, INDEXER_OUTPUT_DIR, INDEXER_PROJECTS
```

After line 214 (end of memory reload block), add:

```python
    ix = _raw.get("indexer", {})
    INDEXER_ENABLED = ix.get("enabled", False)
    INDEXER_OUTPUT_DIR = ix.get("output_dir", "~/conduit-data/indexes")
    INDEXER_PROJECTS = ix.get("projects", [])
```

**Step 4: Verify config loads**

Run: `cd /home/joseph/Projects/conduit && python -c "from server import config; print(config.INDEXER_ENABLED, config.INDEXER_PROJECTS)"`
Expected: `True [{'name': 'spectre', 'path': '~/Projects/spectre'}, {'name': 'conduit', 'path': '~/conduit'}]`

**Step 5: Commit**

```bash
git add server/config.yaml server/config.py
git commit -m "feat: add indexer config section"
```

---

### Task 3: Create indexer module

**Files:**
- Create: `server/indexer.py`

**Step 1: Write the indexer module**

```python
"""Project indexer — scans directories and writes compact YAML indexes.

Used by the load_project_index tool to give agents a pre-built map
of project structure, reducing glob/grep calls from 5-15 to 1-2.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import config

log = logging.getLogger(__name__)

# Directories to always skip
SKIP_DIRS = {
    "node_modules", ".venv", "__pycache__", ".git", ".mypy_cache",
    ".pytest_cache", "dist", "build", ".next", ".svelte-kit",
    "egg-info", ".tox", ".eggs",
}

# Extensions worth describing
CODE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".svelte", ".yaml", ".yml"}

# Max lines to scan for a docstring/comment
DOCSTRING_SCAN_LINES = 5


def _extract_description(filepath: Path) -> str:
    """Extract a one-line description from a source file's docstring or first comment."""
    try:
        with open(filepath, "r", errors="ignore") as f:
            lines = []
            for i, line in enumerate(f):
                if i >= DOCSTRING_SCAN_LINES:
                    break
                lines.append(line.strip())

        text = "\n".join(lines)

        # Python triple-quote docstring
        if '"""' in text:
            start = text.index('"""') + 3
            end = text.index('"""', start) if '"""' in text[start:] else len(text)
            desc = text[start:end].strip().split("\n")[0]
            if desc:
                return desc[:100]

        # Single-line comment (# or //)
        for line in lines:
            if line.startswith("#") and not line.startswith("#!"):
                return line.lstrip("# ").strip()[:100]
            if line.startswith("//"):
                return line.lstrip("/ ").strip()[:100]

    except Exception:
        pass
    return ""


def _scan_directory(root: Path, max_depth: int = 4) -> dict:
    """Recursively scan a directory, building a compact structure map."""
    result = {}

    try:
        entries = sorted(root.iterdir(), key=lambda e: (not e.is_dir(), e.name))
    except PermissionError:
        return result

    for entry in entries:
        if entry.name.startswith(".") and entry.name not in (".env.template",):
            continue

        if entry.is_dir():
            if entry.name in SKIP_DIRS:
                continue
            if max_depth <= 0:
                result[entry.name + "/"] = f"({sum(1 for _ in entry.rglob('*') if _.is_file())} files)"
            else:
                children = _scan_directory(entry, max_depth - 1)
                if children:
                    result[entry.name + "/"] = children
        elif entry.is_file() and entry.suffix in CODE_EXTENSIONS:
            desc = _extract_description(entry)
            size = entry.stat().st_size
            if size > 10000:
                desc_parts = [f"{size // 1000}KB"]
                if desc:
                    desc_parts.append(desc)
                result[entry.name] = " — ".join(desc_parts)
            elif desc:
                result[entry.name] = desc
            else:
                result[entry.name] = ""

    return result


async def index_project(name: str, project_path: str) -> Path | None:
    """Scan a project directory and write a YAML index file."""
    expanded = Path(os.path.expanduser(project_path))
    if not expanded.is_dir():
        log.warning("Indexer: project path does not exist: %s", expanded)
        return None

    output_dir = Path(os.path.expanduser(config.INDEXER_OUTPUT_DIR))
    output_dir.mkdir(parents=True, exist_ok=True)

    structure = _scan_directory(expanded)

    index = {
        "project": name,
        "path": project_path,
        "scanned": datetime.now(timezone.utc).isoformat(),
        "structure": structure,
    }

    output_path = output_dir / f"{name}.yaml"
    with open(output_path, "w") as f:
        yaml.dump(index, f, default_flow_style=False, sort_keys=False, width=120)

    log.info("Indexed project %s → %s", name, output_path)
    return output_path


async def index_all():
    """Index all configured projects."""
    if not config.INDEXER_ENABLED:
        return
    for project in config.INDEXER_PROJECTS:
        await index_project(project["name"], project["path"])
    log.info("Indexer: all projects indexed")
```

**Step 2: Verify module imports**

Run: `cd /home/joseph/Projects/conduit && python -c "from server import indexer; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add server/indexer.py
git commit -m "feat: add project indexer module"
```

---

### Task 4: Register indexer in scheduler

**Files:**
- Modify: `server/scheduler.py:77-78` (add indexer job after memory_consolidation)

**Step 1: Add indexer job**

After line 77 (`log.info("Memory consolidation scheduled weekly (Sun 4am)")`), add:

```python
    # Project indexer — runs daily at 2am
    if config.INDEXER_ENABLED:
        from . import indexer as indexer_mod
        _scheduler.add_job(
            indexer_mod.index_all,
            CronTrigger.from_crontab("0 2 * * *", timezone=config.TIMEZONE),
            id="project_indexer",
            replace_existing=True,
        )
        log.info("Project indexer scheduled daily at 2am")
```

**Step 2: Verify scheduler starts**

Run: `cd /home/joseph/Projects/conduit && python -c "from server import config; print('scheduler tz:', config.TIMEZONE, 'indexer:', config.INDEXER_ENABLED)"`
Expected: `scheduler tz: America/New_York indexer: True`

**Step 3: Commit**

```bash
git add server/scheduler.py
git commit -m "feat: register project indexer as daily scheduled job"
```

---

### Task 5: Add load_project_index tool

**Files:**
- Modify: `server/tools/filesystem.py:312` (add new tool registration)

**Step 1: Add handler function**

After the `_update_index` function (around line 189 in filesystem.py), add:

```python
async def _load_project_index(project: str) -> str:
    """Load a pre-computed project index."""
    from .. import config
    output_dir = Path(os.path.expanduser(config.INDEXER_OUTPUT_DIR))
    index_path = output_dir / f"{project}.yaml"
    if not index_path.exists():
        available = [p.stem for p in output_dir.glob("*.yaml")] if output_dir.exists() else []
        return f"No index found for '{project}'. Available: {', '.join(available) or 'none (run indexer first)'}"
    content = index_path.read_text()
    if len(content) > 50000:
        content = content[:50000] + "\n... (truncated)"
    return content
```

**Step 2: Register the tool**

After line 312 (end of `register_all()`), add:

```python
    register(ToolDefinition(
        name="load_project_index",
        description="Load a pre-computed map of a project's file structure and module descriptions. Use this BEFORE exploring a project with glob/grep — it gives you the full layout in one call.",
        parameters={
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name (e.g. 'spectre', 'conduit')",
                },
            },
            "required": ["project"],
        },
        handler=_load_project_index,
        permission="none",
    ))
```

**Step 3: Update tools_context in app.py**

In `server/app.py`, after line 177 (`lines.append("Use tools when the user asks about files, directories, code, or needs file operations.")`), add:

```python
        lines.append("Use load_project_index before exploring any project — it returns a pre-built map of files and modules.")
```

**Step 4: Test the tool registration**

Run: `cd /home/joseph/Projects/conduit && python -c "from server.tools import filesystem; filesystem.register_all(); from server.tools import get_tool; t = get_tool('load_project_index'); print('registered:', t.name)"`
Expected: `registered: load_project_index`

**Step 5: Commit**

```bash
git add server/tools/filesystem.py server/app.py
git commit -m "feat: add load_project_index tool for agent filesystem navigation"
```

---

### Task 6: Generate initial indexes

**Files:**
- None (runtime task)

**Step 1: Create output directory and run indexer**

```bash
mkdir -p ~/conduit-data/indexes
cd /home/joseph/Projects/conduit
python -c "
import asyncio
from server import indexer
asyncio.run(indexer.index_all())
print('Done')
"
```

**Step 2: Verify index output**

Run: `cat ~/conduit-data/indexes/spectre.yaml | head -30`
Expected: YAML with `project: spectre`, `structure:` tree with file descriptions

Run: `cat ~/conduit-data/indexes/conduit.yaml | head -30`
Expected: YAML with `project: conduit`, `structure:` tree

**Step 3: Test via tool handler**

```bash
cd /home/joseph/Projects/conduit
python -c "
import asyncio
from server.tools.filesystem import _load_project_index
result = asyncio.run(_load_project_index('spectre'))
print(result[:500])
"
```
Expected: First 500 chars of the spectre index YAML

**Step 4: Commit** (no code changes, skip if nothing to commit)

---

### Task 7: Add BM25 config loading

**Files:**
- Modify: `server/config.yaml:156` (add bm25 fields to memory section)
- Modify: `server/config.py:54` (load bm25 config)
- Modify: `server/config.py:163,214` (reload bm25 config)

**Step 1: Add BM25 fields to memory config**

In `server/config.yaml`, after line 156 (`dedup_threshold: 0.9`), add:

```yaml
  bm25_enabled: true
  bm25_db_path: "~/conduit-data/memory_index.db"
  hybrid_top_k: 10
```

**Step 2: Add config loading**

In `server/config.py`, after line 54 (`DEDUP_THRESHOLD = ...`), add:

```python
BM25_ENABLED = memory_cfg.get("bm25_enabled", False)
BM25_DB_PATH = memory_cfg.get("bm25_db_path", "~/conduit-data/memory_index.db")
HYBRID_TOP_K = memory_cfg.get("hybrid_top_k", 10)
```

**Step 3: Add to reload() globals and body**

In `config.py` line 163, add to the global declaration:
```python
    global BM25_ENABLED, BM25_DB_PATH, HYBRID_TOP_K
```

After the memory reload block (around line 214), add:

```python
    BM25_ENABLED = mem.get("bm25_enabled", False)
    BM25_DB_PATH = mem.get("bm25_db_path", "~/conduit-data/memory_index.db")
    HYBRID_TOP_K = mem.get("hybrid_top_k", 10)
```

**Step 4: Verify**

Run: `cd /home/joseph/Projects/conduit && python -c "from server import config; print(config.BM25_ENABLED, config.BM25_DB_PATH)"`
Expected: `True ~/conduit-data/memory_index.db`

**Step 5: Commit**

```bash
git add server/config.yaml server/config.py
git commit -m "feat: add BM25 hybrid memory config"
```

---

### Task 8: Create memory_index module (FTS5 + BM25)

**Files:**
- Create: `server/memory_index.py`

**Step 1: Write the module**

```python
"""SQLite FTS5 memory index — local keyword search mirror for Firestore memories.

Provides BM25 ranking as a complement to Firestore's vector search.
The FTS5 table is a disposable cache: delete the .db file and it
rebuilds from Firestore on next startup via sync_from_firestore().
"""

import logging
import os
import sqlite3
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

_db_path: Path | None = None
_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection | None:
    """Get or create the SQLite connection."""
    global _conn, _db_path
    if _conn is not None:
        return _conn
    if not config.BM25_ENABLED:
        return None
    try:
        _db_path = Path(os.path.expanduser(config.BM25_DB_PATH))
        _db_path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(_db_path), check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                doc_id,
                content,
                category,
                tokenize='porter unicode61'
            )
        """)
        _conn.commit()
        log.info("BM25 memory index ready at %s", _db_path)
        return _conn
    except Exception as e:
        log.error("Failed to initialize BM25 index: %s", e)
        return None


def upsert(doc_id: str, content: str, category: str):
    """Insert or replace a memory in the FTS5 index."""
    conn = _get_conn()
    if not conn:
        return
    try:
        # Delete existing entry if present, then insert
        conn.execute("DELETE FROM memory_fts WHERE doc_id = ?", (doc_id,))
        conn.execute(
            "INSERT INTO memory_fts (doc_id, content, category) VALUES (?, ?, ?)",
            (doc_id, content, category),
        )
        conn.commit()
    except Exception as e:
        log.debug("BM25 upsert failed for %s: %s", doc_id, e)


def delete(doc_id: str):
    """Remove a memory from the FTS5 index."""
    conn = _get_conn()
    if not conn:
        return
    try:
        conn.execute("DELETE FROM memory_fts WHERE doc_id = ?", (doc_id,))
        conn.commit()
    except Exception as e:
        log.debug("BM25 delete failed for %s: %s", doc_id, e)


def search(query: str, top_k: int | None = None) -> list[dict]:
    """BM25 keyword search. Returns [{doc_id, content, category, score}]."""
    conn = _get_conn()
    if not conn:
        return []
    top_k = top_k or config.HYBRID_TOP_K
    try:
        # FTS5 MATCH query — escape special characters
        safe_query = query.replace('"', '""')
        cursor = conn.execute(
            """
            SELECT doc_id, content, category, bm25(memory_fts) as score
            FROM memory_fts
            WHERE memory_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (f'"{safe_query}"', top_k),
        )
        return [
            {"doc_id": row[0], "content": row[1], "category": row[2], "score": row[3]}
            for row in cursor.fetchall()
        ]
    except Exception as e:
        log.debug("BM25 search failed for '%s': %s", query, e)
        return []


async def sync_from_firestore():
    """Full rebuild of FTS5 index from Firestore. Called on startup."""
    if not config.BM25_ENABLED:
        return
    conn = _get_conn()
    if not conn:
        return
    try:
        from . import vectorstore
        memories = await vectorstore.get_all()
        # Clear and rebuild
        conn.execute("DELETE FROM memory_fts")
        for m in memories:
            conn.execute(
                "INSERT INTO memory_fts (doc_id, content, category) VALUES (?, ?, ?)",
                (m["id"], m.get("content", ""), m.get("category", "fact")),
            )
        conn.commit()
        log.info("BM25 index synced: %d memories from Firestore", len(memories))
    except Exception as e:
        log.error("BM25 sync from Firestore failed: %s", e)


def close():
    """Close the SQLite connection."""
    global _conn
    if _conn:
        _conn.close()
        _conn = None
```

**Step 2: Verify module imports**

Run: `cd /home/joseph/Projects/conduit && python -c "from server import memory_index; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add server/memory_index.py
git commit -m "feat: add SQLite FTS5 memory index for BM25 keyword search"
```

---

### Task 9: Wire BM25 into vectorstore write-through

**Files:**
- Modify: `server/vectorstore.py:56-73` (upsert_memory — add FTS5 write-through)
- Modify: `server/vectorstore.py:338-346` (delete — add FTS5 delete)

**Step 1: Add write-through to upsert_memory**

In `server/vectorstore.py`, after line 73 (end of the `set()` call in `upsert_memory`), add:

```python
    # Write-through to BM25 index
    from . import memory_index
    memory_index.upsert(doc_id, content, category)
```

So lines 60-76 become:
```python
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
```

**Step 2: Add delete-through to delete**

In `server/vectorstore.py`, after line 344 (`await doc_ref.delete()`), add:

```python
        # Delete from BM25 index
        from . import memory_index
        memory_index.delete(doc_id)
```

**Step 3: Commit**

```bash
git add server/vectorstore.py
git commit -m "feat: write-through BM25 index on memory upsert/delete"
```

---

### Task 10: Wire hybrid retrieval into memory.py

**Files:**
- Modify: `server/memory.py:215-286` (get_memory_context — add BM25 path + RRF merge)

**Step 1: Replace get_memory_context**

Replace the function at lines 215-286 with:

```python
async def get_memory_context(query: str = "") -> str:
    """Build formatted memory context for system prompt injection.

    Uses hybrid retrieval: semantic search (Firestore KNN) + BM25 keyword
    search (SQLite FTS5), merged via Reciprocal Rank Fusion.
    Falls back to high-importance memories when both return too few results.
    """
    if not vectorstore.is_available():
        return ""

    try:
        memories_by_id: dict[str, dict] = {}
        semantic_count = 0

        if query:
            # Path 1: Semantic search (existing)
            try:
                query_embedding = await embeddings.embed_query(query)
                results = await vectorstore.vector_search(query_embedding, config.SEARCH_TOP_K)
                for i, m in enumerate(results):
                    m["_semantic_rank"] = i
                    memories_by_id[m["id"]] = m
                semantic_count = len(memories_by_id)
            except Exception as e:
                log.warning("Semantic search failed: %s", e)

            # Path 2: BM25 keyword search (new)
            if config.BM25_ENABLED:
                try:
                    from . import memory_index
                    bm25_results = memory_index.search(query, config.HYBRID_TOP_K)
                    for i, bm in enumerate(bm25_results):
                        doc_id = bm["doc_id"]
                        if doc_id in memories_by_id:
                            # Found by both — record BM25 rank for fusion
                            memories_by_id[doc_id]["_bm25_rank"] = i
                        else:
                            # Only found by BM25 — fetch full doc from Firestore
                            full = await vectorstore.get_by_id(doc_id)
                            if full:
                                full["_bm25_rank"] = i
                                memories_by_id[doc_id] = full
                except Exception as e:
                    log.debug("BM25 search failed: %s", e)

            # Reciprocal Rank Fusion scoring
            if config.BM25_ENABLED and memories_by_id:
                k = 60  # Standard RRF constant
                for m in memories_by_id.values():
                    sem_rank = m.get("_semantic_rank", 999)
                    bm_rank = m.get("_bm25_rank", 999)
                    m["_rrf_score"] = (1 / (k + sem_rank)) + (1 / (k + bm_rank))

                # Sort by RRF score descending, take top N
                ranked = sorted(
                    memories_by_id.values(),
                    key=lambda x: x.get("_rrf_score", 0),
                    reverse=True,
                )[:config.HYBRID_TOP_K]
                memories_by_id = {m["id"]: m for m in ranked}
                semantic_count = len(memories_by_id)

        # Fall back to high-importance if we have fewer than 3 results
        if semantic_count < 3:
            high = await vectorstore.get_high_importance_recent(
                config.IMPORTANCE_FLOOR, limit=5,
            )
            for m in high:
                memories_by_id.setdefault(m["id"], m)

        if not memories_by_id:
            return ""

        # Touch accessed memories in the background
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
```

**Step 2: Commit**

```bash
git add server/memory.py
git commit -m "feat: hybrid retrieval with BM25 + semantic via Reciprocal Rank Fusion"
```

---

### Task 11: Wire BM25 startup sync into app.py

**Files:**
- Modify: `server/app.py` (add BM25 sync to startup lifespan)

**Step 1: Find the lifespan startup section**

In `app.py`, locate the `lifespan()` context manager. After the vectorstore/memory initialization block (around the scheduler start), add:

```python
    # Sync BM25 index from Firestore
    if config.BM25_ENABLED:
        try:
            from . import memory_index
            await memory_index.sync_from_firestore()
            log.info("BM25 memory index synced from Firestore")
        except Exception as e:
            log.warning("BM25 sync failed (non-fatal): %s", e)
```

**Step 2: Add cleanup in shutdown section**

In the shutdown part of `lifespan()`, add:

```python
    # Close BM25 index
    try:
        from . import memory_index
        memory_index.close()
    except Exception:
        pass
```

**Step 3: Commit**

```bash
git add server/app.py
git commit -m "feat: sync BM25 index from Firestore on startup"
```

---

### Task 12: Verify vectorstore.get_by_id exists

**Files:**
- Possibly modify: `server/vectorstore.py`

**Step 1: Check if get_by_id exists**

Run: `cd /home/joseph/Projects/conduit && grep -n "def get_by_id\|async def get_by_id" server/vectorstore.py`

If it exists, skip to Step 3. If not:

**Step 2: Add get_by_id function**

Add after the `vector_search` function (around line 100):

```python
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
```

**Step 3: Verify get_all exists too** (needed for BM25 sync)

Run: `cd /home/joseph/Projects/conduit && grep -n "def get_all" server/vectorstore.py`

If missing, add a `get_all()` function that fetches all memories from the collection.

**Step 4: Commit if changes made**

```bash
git add server/vectorstore.py
git commit -m "feat: add get_by_id and get_all to vectorstore"
```

---

### Task 13: Deploy to tablet

**Step 1: Push to remote**

```bash
cd /home/joseph/Projects/conduit
git push
```

**Step 2: Pull on tablet and restart**

```bash
ssh -p 8022 192.168.1.174 "cd ~/conduit && git pull --ff-only && ~/conduit-tablet/scripts/update.sh"
```

**Step 3: Run initial index generation on tablet**

```bash
ssh -p 8022 192.168.1.174 "cd ~/conduit && python -c \"
import asyncio
from server import indexer
asyncio.run(indexer.index_all())
print('Indexes generated')
\""
```

**Step 4: Verify server is healthy**

```bash
ssh -p 8022 192.168.1.174 "~/conduit-tablet/scripts/status.sh"
```

Expected: All services UP/RUNNING

**Step 5: Test via chat**

Send a message to Conduit and verify:
- Response comes from NIM (GLM-5) — check server logs for provider name
- Memory retrieval works (no errors in logs)

---

## Implementation Order Summary

| Task | Feature | Effort | Dependencies |
|------|---------|--------|--------------|
| 1 | GLM-5 routing | trivial | none |
| 2 | Indexer config | trivial | none |
| 3 | Indexer module | small | task 2 |
| 4 | Indexer scheduler | trivial | task 3 |
| 5 | Index tool | small | task 3 |
| 6 | Generate indexes | trivial | task 5 |
| 7 | BM25 config | trivial | none |
| 8 | Memory index module | medium | task 7 |
| 9 | Vectorstore write-through | small | task 8 |
| 10 | Hybrid retrieval | medium | task 8, 9 |
| 11 | Startup sync | small | task 8 |
| 12 | Vectorstore helpers | small | task 10 needs it |
| 13 | Deploy | trivial | all above |
