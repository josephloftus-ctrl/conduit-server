# Plugins, Skills & Subagents — Design

**Date:** 2026-02-16
**Status:** Approved

---

## Overview

Three capabilities inspired by OpenClaw's architecture, built natively in Python for Conduit:

1. **Markdown Skills** — OpenClaw-compatible `SKILL.md` files injected into agent context
2. **Python Plugins** — Executable modules that register tools, hooks, and skills
3. **Subagent System** — Isolated session spawning with full lifecycle management

---

## Constraints (Tablet Production)

- No venv on tablet — new deps must be pure Python or Termux-available
- Config overlay pattern: develop with sensible defaults, tablet overrides paths
- Firestore uses REST patch on tablet — new Firestore interactions go through existing vectorstore
- Graceful degradation when dependencies are missing (lazy imports, try/except)
- SQLite for persistence (proven with BM25 index)

---

## Feature 1: Markdown Skills

### Format (OpenClaw Compatible)

Skills live in a configurable directory. Each skill is a folder with a `SKILL.md`:

```
~/.conduit/skills/
  weather/
    SKILL.md
  food-order/
    SKILL.md
```

`SKILL.md` uses YAML frontmatter + markdown body (same format as OpenClaw/ClawHub):

```yaml
---
name: weather
description: Look up current weather conditions
metadata:
  openclaw:
    requires:
      bins: ["curl"]
  conduit:
    agent: default
---

# Weather

When the user asks about weather...
```

### Discovery & Loading

New module `server/skills.py`:

- On startup, scan skills directory for `*/SKILL.md` files
- Parse YAML frontmatter via `yaml.safe_load()` (PyYAML, already a dependency)
- Check `requires.bins` — skip skills whose binaries aren't on PATH
- Store parsed skills in memory: `{name, description, body, agent_affinity, requires}`

### Context Injection

New `{skills_context}` placeholder in system prompt template.

Built at message time in `app.py`:

1. **Catalog line**: always included — list of all loaded skills as `"Available skills: weather (weather lookup), food-order (order food), ..."`
2. **Full injection**: when the user's message matches a skill by keyword or the classifier (Haiku) flags relevance, include the full markdown body for that skill (max 2 skills per turn to limit prompt size)
3. **Agent affinity**: if a skill declares `conduit.agent`, route to that agent when the skill is triggered

### ClawHub Fetch

A built-in tool `skill_install` lets agents (or the user via slash command) install skills:

```python
async def _skill_install(name: str, source: str = "clawhub") -> str:
    """Install a skill from ClawHub or a URL."""
```

For ClawHub: `GET https://clawhub.com/api/skills/{slug}/download` → save to skills directory.
For URL: fetch and save.

Also a CLI-style `/install <name>` command binding.

### Config

```yaml
skills:
  enabled: true
  dir: "~/.conduit/skills"
  max_per_turn: 2
```

Tablet overlay:

```yaml
skills:
  enabled: true
  dir: "~/conduit-data/skills"
  max_per_turn: 2
```

---

## Feature 2: Python Plugins

### Format

Plugins live in a configurable directory. Each plugin is a Python package:

```
~/.conduit/plugins/
  my-plugin/
    plugin.json
    __init__.py
```

`plugin.json` manifest:

```json
{
  "id": "my-plugin",
  "name": "My Plugin",
  "description": "Does something useful",
  "version": "1.0.0",
  "config_schema": {
    "api_key_env": {"type": "string", "description": "API key env var"}
  }
}
```

`__init__.py` exports a `register` function:

```python
from conduit.plugin_sdk import PluginAPI

def register(api: PluginAPI):
    api.register_tool(
        name="my_tool",
        description="Does the thing",
        parameters={"type": "object", "properties": {...}, "required": [...]},
        handler=my_handler,
        permission="none",
    )
```

### Plugin API

```python
@dataclass
class PluginAPI:
    id: str
    config: dict  # plugin-specific config resolved from plugin.json schema

    def register_tool(self, name: str, description: str, parameters: dict,
                      handler: Callable[..., Awaitable[str]],
                      permission: str = "none") -> None:
        """Register a tool into Conduit's global tool registry."""

    def register_hook(self, event: str, handler: Callable) -> None:
        """Register a lifecycle hook handler."""

    def register_skill(self, name: str, description: str, content: str) -> None:
        """Programmatically register a skill (alternative to SKILL.md)."""

    def log(self, level: str, message: str) -> None:
        """Log a message under the plugin's namespace."""
```

### Hook Events

| Hook | When | Handler signature |
|------|------|-------------------|
| `on_startup` | Server starts, after plugin load | `async () -> None` |
| `on_shutdown` | Server stops | `async () -> None` |
| `before_agent_start` | Before LLM call | `async (messages, system_prompt) -> dict | None` — can return `{"system_prompt": modified}` |
| `after_tool_call` | After tool executes | `async (tool_name, args, result) -> str | None` — can transform result |
| `message_received` | User message arrives | `async (content, conversation_id) -> str | None` — can transform content |
| `message_sending` | Before response to user | `async (content) -> str | None` — can transform or return None to cancel |

### Discovery & Loading

New module `server/plugins.py`:

1. On startup, scan plugins directory for directories containing `plugin.json`
2. Validate manifest (require `id` field)
3. Add plugin directory to `sys.path` temporarily
4. Import the module via `importlib.import_module()`
5. Call `register(api)` with a `PluginAPI` instance
6. Registered tools go into global `_TOOLS` registry (same as built-in tools)
7. Registered hooks go into `_hooks: dict[str, list[Callable]]` event bus
8. On failure: log warning, skip plugin, continue loading others

### Hook Dispatch

New function in `plugins.py`:

```python
async def dispatch_hook(event: str, **kwargs) -> dict | None:
    """Run all handlers for an event. Returns merged modifications or None."""
```

Called at appropriate points in `agent.py` and `app.py`.

### Config

```yaml
plugins:
  enabled: true
  dir: "~/.conduit/plugins"
```

Tablet overlay:

```yaml
plugins:
  enabled: true
  dir: "~/conduit-data/plugins"
```

---

## Feature 3: Subagent System

### Session Model

```python
@dataclass
class SubagentSession:
    run_id: str                     # UUID hex (12 chars)
    child_agent_id: str             # which agent runs the task
    parent_session_key: str         # who spawned this
    parent_agent_id: str
    task: str                       # original request
    label: str | None               # human-readable name
    model_override: str | None      # optional provider override
    messages: list[dict]            # isolated conversation history
    status: str                     # "running" | "done" | "error" | "timeout"
    result: str | None              # final assistant reply
    depth: int                      # 0=top-level, 1=subagent, 2=sub-subagent
    children: list[str]             # run_ids of spawned children
    cleanup: str                    # "delete" | "keep"
    created_at: float
    started_at: float | None
    ended_at: float | None
    timeout_seconds: int
```

### Registry

`server/subagents.py` maintains sessions in memory with SQLite persistence:

```python
_sessions: dict[str, SubagentSession] = {}
```

```sql
CREATE TABLE IF NOT EXISTS subagent_sessions (
    run_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    created_at REAL NOT NULL,
    ended_at REAL
);
```

- Active sessions live in memory
- Completed sessions persist to SQLite, evict from memory after `session_ttl_minutes` (default 60)
- On startup, incomplete sessions from crashes are marked `"error"`
- Periodic sweeper cleans expired sessions

### Spawning Flow

1. Agent calls `sessions_spawn(agent_id, task, ...)` tool
2. **Depth check**: parent depth from registry; reject if `>= max_spawn_depth`
3. **Children limit**: count active children; reject if `>= max_children`
4. **Allowlist**: check parent agent's `subagents.allow_agents` (default `["*"]`)
5. Create `SubagentSession` with `depth = parent_depth + 1`, empty `messages`
6. Build child system prompt with subagent preamble explaining task scope
7. Launch `asyncio.create_task(_run_subagent(session))`
8. Return immediately: `"Spawned: {run_id} (agent: {agent_id}, label: {label})"`

### Execution

`_run_subagent(session)`:

- Resolve child agent's provider and filtered tools (including plugin tools)
- Build system prompt with agent's prompt_extend + subagent preamble
- Run `run_agent_loop()` with isolated `session.messages`, `SilentAdapter`
- Wrap in `asyncio.wait_for(timeout_seconds)`
- On complete: `status="done"`, `result=last_assistant_message`
- On error: `status="error"`, `result=error_message`
- On timeout: `status="timeout"`, `result="Timed out after {n}s"`
- Persist to SQLite
- Queue announcement for parent

### Recursive Spawning

Subagents get `sessions_spawn` in their tools if `depth < max_spawn_depth`. A subagent at depth 1 with `max_spawn_depth=2` can spawn sub-subagents at depth 2.

When a child finishes but has active grandchildren, its announcement to the parent is **deferred** until all descendants complete.

### Announcement Queue

```python
_pending_announces: dict[str, list[dict]] = {}  # parent_session_key -> announcements
```

On child completion:
1. Build announcement: `"[Subagent Complete] \"{label}\" ({status}). Result: {summary}"`
2. If parent is mid-turn (check via a flag): queue in `_pending_announces`
3. If parent is idle: announcement injected at next `handle_message()` call

`get_pending_announces(parent_key)` called at top of message handling to drain queue and prepend to conversation.

### Inter-Session Messaging

`sessions_send` sends a message to a running session by `run_id` or `label`. The message is appended to the child's isolated `messages` as a user message, the child agent runs a turn, and the reply is returned synchronously to the caller.

### Tools

Five tools replace the current `agent_send`/`agent_spawn`/`agent_get_result`:

| Tool | Parameters | Description |
|------|-----------|-------------|
| `sessions_spawn` | `agent_id, task, label?, model?, timeout?, cleanup?` | Spawn subagent. Returns run_id. |
| `sessions_send` | `run_id_or_label, message, timeout?` | Send message to running session. Returns reply. |
| `sessions_list` | `status_filter?` | List sessions with status, label, agent_id, runtime. |
| `sessions_history` | `run_id` | Get conversation history (capped at 10k chars). |
| `sessions_kill` | `run_id` | Cancel a running session. |

### Config

```yaml
agents:
  subagents:
    enabled: true
    max_spawn_depth: 2
    max_children: 5
    default_timeout: 300
    session_ttl_minutes: 60
```

Per-agent override:

```yaml
agents:
  list:
    - id: analyst
      provider: opus
      subagents:
        allow_agents: ["researcher", "operations"]
        max_children: 3
```

---

## Integration Points

### System Prompt

```yaml
personality:
  system_prompt: |
    ...
    {tools_context}
    {skills_context}
    {scout_context}
```

### Plugin Tools in Agent Filtering

Plugin-registered tools enter the same global `_TOOLS` registry. `tools_allow`/`tools_deny` filters apply to them identically.

### Skill-Aware Routing

Skills with `conduit.agent` affinity trigger agent routing when the classifier detects relevance.

### Subagent + Plugin Interaction

Subagents inherit the child agent's plugin tools (filtered by `tools_allow`). Skills are per-agent based on the child's identity.

---

## Files Summary

| File | Change | Feature |
|------|--------|---------|
| `server/skills.py` | NEW: skill discovery, parsing, context injection, ClawHub fetch | Skills |
| `server/plugins.py` | NEW: plugin discovery, loading, PluginAPI, hook bus | Plugins |
| `server/subagents.py` | NEW: session registry, spawning, execution, announcements | Subagents |
| `server/config.yaml` | Modify: add skills, plugins, agents.subagents sections | All |
| `server/config.py` | Modify: new constants for skills/plugins/subagents + reload | All |
| `server/app.py` | Modify: plugin loading, skill context, announcement drain, subagent tools | All |
| `server/agents.py` | Modify: remove old comms tools, subagent per-agent config, new tools injection | Subagents |
| `server/agent.py` | Modify: hook dispatch (before_agent_start, after_tool_call, message_sending) | Plugins |

## Implementation Order

1. **Skills** — smallest, independent, immediate value from ClawHub
2. **Plugins** — builds on tool registry, enables custom extensibility
3. **Subagents** — most complex, depends on agents.py refactor
