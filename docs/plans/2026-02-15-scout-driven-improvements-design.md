# Scout-Driven Improvements — Design

**Date:** 2026-02-15
**Status:** Approved
**Source:** Reddit Scout Report (2026-02-15) — findings #2, #1+#5, #4

---

## Overview

Three improvements to Conduit based on Reddit Scout findings:

1. **GLM-5 as primary provider** via NVIDIA NIM, MiniMax 2.5 as fallback
2. **Pre-computed project indexes** to reduce filesystem tool calls
3. **BM25 hybrid memory retrieval** to catch keyword matches semantic search misses

---

## Feature 1: GLM-5 Primary + MiniMax Fallback

**Effort:** Trivial (config only)
**Code changes:** Zero

Update `config.yaml`:

```yaml
nim:
  type: openai_compat
  base_url: "https://integrate.api.nvidia.com/v1"
  api_key_env: "NIM_API_KEY"
  default_model: "z-ai/glm-5"
  role: primary

routing:
  default: "nim"
  fallback_chain: ["nim", "minimax", "openrouter", "deepseek-free", "ollama"]
  long_context: "gemini"
  escalation: "opus"
  brain: "haiku"
```

When NIM returns rate-limit or credit-exhausted errors, `stream_with_fallback` catches it and moves to MiniMax (`minimax/minimax-m2.5` via OpenRouter). Rest of chain stays as safety net.

---

## Feature 2: Pre-Computed Project Indexes

**Effort:** Small
**New files:** `server/indexer.py`, `tools/filesystem.py` addition
**Config:** New `indexer` section

### Index Generator

New module `server/indexer.py` scans configured project directories and writes compact YAML:

```yaml
# ~/conduit-data/indexes/spectre.yaml (auto-generated)
project: spectre
path: ~/Projects/spectre
scanned: "2026-02-15T20:00:00"
structure:
  backend/:
    core/:
      menu_planning/: "SQLAlchemy menu recommendation engine"
      plugins/: "Client config loader (singleton pattern)"
    api/: "28 FastAPI routers"
    flag_checker.py: "Item/room health scoring (591 lines)"
    classifier.py: "ABC-XYZ inventory classification"
    purchase_match.py: "SKU validation against vendor catalogs"
    scores.py: "Score persistence and snapshots"
    worker.py: "Background job processor"
  frontend/src/:
    pages/: "React pages (dashboard, inbox, count, etc.)"
    components/: "UI components (shadcn/ui based)"
    api.ts: "API client (1505 lines)"
  plugins/culinart/:
    mogs/: "Master Option Group definitions"
    sites.yaml: "Site configurations"
```

**How it works:**
- Walks file tree, skips `node_modules`, `.venv`, `__pycache__`, `.git`
- For Python/JS/TS files: extracts module docstring or first comment as description
- For directories: counts files, notes key patterns
- Output capped at ~200 lines per project

### New Tool: `load_project_index`

```python
async def load_project_index(project: str) -> str:
    """Load pre-computed index for a project.
    Available projects: spectre, conduit, conduit-tablet"""
    index_path = f"~/conduit-data/indexes/{project}.yaml"
    return read_file(index_path)
```

Agent calls this once at the start of a task instead of exploratory glob/grep.

### Scheduler Integration

Runs daily or on `update.sh` after git pull.

```yaml
indexer:
  enabled: true
  projects:
    - path: "~/Projects/spectre"
      name: spectre
    - path: "~/conduit"
      name: conduit
  output_dir: "~/conduit-data/indexes"
```

### Tool Context

Update `{tools_context}` to mention: *"Use `load_project_index` before exploring a project to get a pre-built map of files and modules."*

---

## Feature 3: BM25 + Firestore Hybrid Memory Retrieval

**Effort:** Medium
**New files:** `server/memory_index.py`
**Modified files:** `server/memory.py`, `server/vectorstore.py`
**Config:** New fields in `memory` section

### Local SQLite FTS5 Mirror

New file `server/memory_index.py` maintains a local SQLite database:

```sql
CREATE VIRTUAL TABLE memory_fts USING fts5(
    doc_id,
    content,
    category,
    tokenize='porter unicode61'
);
```

**Sync strategy:**
- On memory create/update/delete in `vectorstore.py` → also write to FTS5
- On startup → full sync from Firestore to rebuild FTS5 (cold start recovery)
- FTS5 is a read-optimization cache; Firestore remains source of truth

### BM25 Search

```python
async def bm25_search(query: str, top_k: int = 8) -> list[dict]:
    """Keyword search using SQLite FTS5 BM25 ranking."""
    sql = """
        SELECT doc_id, content, category, bm25(memory_fts) as score
        FROM memory_fts
        WHERE memory_fts MATCH ?
        ORDER BY score
        LIMIT ?
    """
```

### Hybrid Retrieval

Replace single-path retrieval in `memory.py` with two-path merge:

1. **Semantic path** (existing): Embed query → Firestore KNN → ranked results
2. **BM25 path** (new): FTS5 keyword search → ranked results
3. **Merge**: Reciprocal Rank Fusion (RRF) with `k=60`

```python
# Reciprocal Rank Fusion
hybrid_score = (1 / (60 + semantic_rank)) + (1 / (60 + bm25_rank))
```

- Memories found by both methods get naturally boosted
- Memories found only by BM25 (exact keyword hit) still surface
- No tuning needed — `k=60` is well-established for RRF

Final merged results: top 10.

### Config

```yaml
memory:
  bm25_enabled: true
  bm25_db_path: "~/conduit-data/memory_index.db"
  hybrid_top_k: 10
```

### What Doesn't Change

- Firestore remains single source of truth
- Memory extraction (Haiku), decay, consolidation — unchanged
- Embedding model and dimensions — unchanged
- FTS5 table is disposable — delete it and it rebuilds from Firestore on startup

---

## Files Summary

| File | Change | Feature |
|------|--------|---------|
| `server/config.yaml` | Modify: routing, provider, indexer, memory sections | All 3 |
| `server/indexer.py` | New: project index generator | #2 |
| `server/memory_index.py` | New: SQLite FTS5 mirror + BM25 search | #3 |
| `server/memory.py` | Modify: hybrid retrieval in `get_memory_context` | #3 |
| `server/vectorstore.py` | Modify: write-through to FTS5 on create/update/delete | #3 |
| `tools/filesystem.py` | Modify: add `load_project_index` tool | #2 |
| `tools/definitions.py` | Modify: add tool definition for `load_project_index` | #2 |

## Implementation Order

1. Feature 1 (config change) — deploy immediately
2. Feature 2 (indexer) — small, independent
3. Feature 3 (BM25 hybrid) — medium, most impactful
