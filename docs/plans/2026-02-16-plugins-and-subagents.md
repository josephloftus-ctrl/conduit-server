# Plugins, Skills & Subagents — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add OpenClaw-compatible markdown skills, a native Python plugin system, and an isolated subagent session system to Conduit.

**Architecture:** Three new modules (`server/skills.py`, `server/plugins.py`, `server/subagents.py`) integrate with the existing tool registry, config loader, agent loop, and system prompt. Skills inject markdown context into prompts, plugins register tools/hooks via a `PluginAPI`, and subagents spawn isolated child sessions with SQLite persistence.

**Tech Stack:** Python 3.11+, PyYAML (existing dep), aiosqlite (existing dep), asyncio, dataclasses. No new dependencies.

**Design doc:** `docs/plans/2026-02-16-plugins-and-subagents-design.md`

**Tablet constraints:** No venv (system-wide pip), graceful degradation via try/except lazy imports, config overlay pattern (`conduit-tablet/config.yaml` overrides paths), SQLite for persistence.

---

## Task 0: Test Infrastructure Setup

**Files:**
- Create: `server/tests/__init__.py`
- Create: `server/tests/conftest.py`

**Step 1: Create test package and shared fixtures**

Create `server/tests/__init__.py`:

```python
```

Create `server/tests/conftest.py`:

```python
"""Shared test fixtures for Conduit server tests."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure server package is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


@pytest.fixture
def tmp_skills_dir(tmp_path):
    """Create a temporary skills directory with sample skills."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    return skills_dir


@pytest.fixture
def tmp_plugins_dir(tmp_path):
    """Create a temporary plugins directory."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    return plugins_dir


@pytest.fixture
def sample_skill_md():
    """Return a valid SKILL.md string."""
    return """---
name: weather
description: Look up current weather conditions
metadata:
  conduit:
    agent: default
---

# Weather

When the user asks about weather, use a web search to find current conditions.
"""


@pytest.fixture
def sample_plugin_manifest():
    """Return a valid plugin.json dict."""
    return {
        "id": "test-plugin",
        "name": "Test Plugin",
        "description": "A test plugin",
        "version": "1.0.0",
    }
```

**Step 2: Verify pytest can discover tests**

Run: `cd /home/joseph/Projects/conduit && python -m pytest server/tests/ --collect-only 2>&1 | head -20`
Expected: "no tests ran" (no test files yet, but no import errors)

**Step 3: Commit**

```bash
git add server/tests/__init__.py server/tests/conftest.py
git commit -m "chore: add test infrastructure for skills/plugins/subagents"
```

---

## Task 1: Config — New Sections for Skills, Plugins, Subagents

**Files:**
- Modify: `server/config.yaml:192-198` (replace old skills section, add new sections)
- Modify: `server/config.py:82-92` (add new config globals)
- Modify: `server/config.py:165-258` (add to reload function)
- Test: `server/tests/test_config_new_sections.py`

**Step 1: Write the failing test**

Create `server/tests/test_config_new_sections.py`:

```python
"""Tests for new config sections: markdown_skills, plugins, subagents."""

import yaml
import tempfile
from pathlib import Path


def test_config_has_markdown_skills_section():
    """Config should expose MARKDOWN_SKILLS_* constants."""
    from server import config
    assert hasattr(config, "MARKDOWN_SKILLS_ENABLED")
    assert hasattr(config, "MARKDOWN_SKILLS_DIR")
    assert hasattr(config, "MARKDOWN_SKILLS_MAX_PER_TURN")


def test_config_has_plugins_section():
    """Config should expose PLUGINS_* constants."""
    from server import config
    assert hasattr(config, "PLUGINS_ENABLED")
    assert hasattr(config, "PLUGINS_DIR")


def test_config_has_subagents_section():
    """Config should expose SUBAGENTS_* constants."""
    from server import config
    assert hasattr(config, "SUBAGENTS_ENABLED")
    assert hasattr(config, "SUBAGENTS_MAX_SPAWN_DEPTH")
    assert hasattr(config, "SUBAGENTS_MAX_CHILDREN")
    assert hasattr(config, "SUBAGENTS_DEFAULT_TIMEOUT")
    assert hasattr(config, "SUBAGENTS_SESSION_TTL_MINUTES")


def test_config_defaults_are_sensible():
    """Default values should be usable without config.yaml changes."""
    from server import config
    assert config.MARKDOWN_SKILLS_ENABLED is True
    assert "skills" in config.MARKDOWN_SKILLS_DIR
    assert config.MARKDOWN_SKILLS_MAX_PER_TURN == 2
    assert config.PLUGINS_ENABLED is True
    assert "plugins" in config.PLUGINS_DIR
    assert config.SUBAGENTS_ENABLED is True
    assert config.SUBAGENTS_MAX_SPAWN_DEPTH == 2
    assert config.SUBAGENTS_MAX_CHILDREN == 5
    assert config.SUBAGENTS_DEFAULT_TIMEOUT == 300
    assert config.SUBAGENTS_SESSION_TTL_MINUTES == 60
```

**Step 2: Run test to verify it fails**

Run: `cd /home/joseph/Projects/conduit && python -m pytest server/tests/test_config_new_sections.py -v`
Expected: FAIL with `AttributeError: module 'server.config' has no attribute 'MARKDOWN_SKILLS_ENABLED'`

**Step 3: Add new sections to config.yaml**

Modify `server/config.yaml`. Replace lines 192-198 (old `skills:` section) with:

```yaml
skills:
  grocery:
    enabled: true
  expenses:
    enabled: true
  calendar:
    enabled: true

markdown_skills:
  enabled: true
  dir: "~/.conduit/skills"
  max_per_turn: 2

plugins:
  enabled: true
  dir: "~/.conduit/plugins"
```

And add subagents config inside the existing `agents:` section. After `agents.communication` (after line 125), add:

```yaml
  subagents:
    enabled: true
    max_spawn_depth: 2
    max_children: 5
    default_timeout: 300
    session_ttl_minutes: 60
```

**Step 4: Add new config globals to config.py**

After the existing skills section (line 92 in `config.py`), add:

```python
# Markdown Skills (OpenClaw compatible)
md_skills_cfg = _raw.get("markdown_skills", {})
MARKDOWN_SKILLS_ENABLED = md_skills_cfg.get("enabled", True)
MARKDOWN_SKILLS_DIR = md_skills_cfg.get("dir", "~/.conduit/skills")
MARKDOWN_SKILLS_MAX_PER_TURN = md_skills_cfg.get("max_per_turn", 2)

# Plugins
plugins_cfg = _raw.get("plugins", {})
PLUGINS_ENABLED = plugins_cfg.get("enabled", True)
PLUGINS_DIR = plugins_cfg.get("dir", "~/.conduit/plugins")

# Subagents
subagents_cfg = agents_cfg.get("subagents", {})
SUBAGENTS_ENABLED = subagents_cfg.get("enabled", True)
SUBAGENTS_MAX_SPAWN_DEPTH = subagents_cfg.get("max_spawn_depth", 2)
SUBAGENTS_MAX_CHILDREN = subagents_cfg.get("max_children", 5)
SUBAGENTS_DEFAULT_TIMEOUT = subagents_cfg.get("default_timeout", 300)
SUBAGENTS_SESSION_TTL_MINUTES = subagents_cfg.get("session_ttl_minutes", 60)
```

Add these same globals to `reload()` — both the `global` declaration (line 178 area) and the re-assignment block (line 255 area):

Add to the global declaration line:

```python
global MARKDOWN_SKILLS_ENABLED, MARKDOWN_SKILLS_DIR, MARKDOWN_SKILLS_MAX_PER_TURN
global PLUGINS_ENABLED, PLUGINS_DIR
global SUBAGENTS_ENABLED, SUBAGENTS_MAX_SPAWN_DEPTH, SUBAGENTS_MAX_CHILDREN
global SUBAGENTS_DEFAULT_TIMEOUT, SUBAGENTS_SESSION_TTL_MINUTES
```

Add to the reload body (after the existing skills reload block at line 258):

```python
mds = _raw.get("markdown_skills", {})
MARKDOWN_SKILLS_ENABLED = mds.get("enabled", True)
MARKDOWN_SKILLS_DIR = mds.get("dir", "~/.conduit/skills")
MARKDOWN_SKILLS_MAX_PER_TURN = mds.get("max_per_turn", 2)

plg = _raw.get("plugins", {})
PLUGINS_ENABLED = plg.get("enabled", True)
PLUGINS_DIR = plg.get("dir", "~/.conduit/plugins")

sub = ag.get("subagents", {})
SUBAGENTS_ENABLED = sub.get("enabled", True)
SUBAGENTS_MAX_SPAWN_DEPTH = sub.get("max_spawn_depth", 2)
SUBAGENTS_MAX_CHILDREN = sub.get("max_children", 5)
SUBAGENTS_DEFAULT_TIMEOUT = sub.get("default_timeout", 300)
SUBAGENTS_SESSION_TTL_MINUTES = sub.get("session_ttl_minutes", 60)
```

**Step 5: Run test to verify it passes**

Run: `cd /home/joseph/Projects/conduit && python -m pytest server/tests/test_config_new_sections.py -v`
Expected: PASS (4 tests)

**Step 6: Commit**

```bash
git add server/config.py server/config.yaml server/tests/test_config_new_sections.py
git commit -m "feat: add config sections for markdown skills, plugins, and subagents"
```

---

## Task 2: Skill Discovery & Parsing (`server/skills.py`)

**Files:**
- Create: `server/skills.py`
- Test: `server/tests/test_skills.py`

**Step 1: Write the failing tests**

Create `server/tests/test_skills.py`:

```python
"""Tests for markdown skill discovery and parsing."""

import os
from pathlib import Path


def _make_skill(skills_dir: Path, name: str, content: str):
    """Helper: create a skill directory with SKILL.md."""
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content)


def test_parse_skill_md_valid(tmp_skills_dir, sample_skill_md):
    """parse_skill_md should extract frontmatter and body."""
    from server.skills import parse_skill_md
    skill = parse_skill_md(sample_skill_md, "weather")
    assert skill is not None
    assert skill["name"] == "weather"
    assert skill["description"] == "Look up current weather conditions"
    assert "When the user asks about weather" in skill["body"]
    assert skill["agent_affinity"] == "default"


def test_parse_skill_md_no_frontmatter():
    """parse_skill_md should return None for invalid SKILL.md."""
    from server.skills import parse_skill_md
    result = parse_skill_md("# Just markdown, no frontmatter", "bad")
    assert result is None


def test_parse_skill_md_missing_name():
    """parse_skill_md should fall back to folder name if name missing."""
    from server.skills import parse_skill_md
    content = """---
description: Does something
---

# Content here
"""
    skill = parse_skill_md(content, "fallback-name")
    assert skill["name"] == "fallback-name"


def test_discover_skills(tmp_skills_dir, sample_skill_md):
    """discover_skills should find all valid SKILL.md files."""
    from server.skills import discover_skills
    _make_skill(tmp_skills_dir, "weather", sample_skill_md)
    _make_skill(tmp_skills_dir, "bad-skill", "# No frontmatter")
    _make_skill(tmp_skills_dir, "food-order", """---
name: food-order
description: Order food for delivery
---

# Food Order

Help the user order food.
""")
    skills = discover_skills(str(tmp_skills_dir))
    assert len(skills) == 2
    names = {s["name"] for s in skills}
    assert names == {"weather", "food-order"}


def test_discover_skills_empty_dir(tmp_skills_dir):
    """discover_skills should return empty list for empty directory."""
    from server.skills import discover_skills
    skills = discover_skills(str(tmp_skills_dir))
    assert skills == []


def test_discover_skills_missing_dir():
    """discover_skills should return empty list for nonexistent directory."""
    from server.skills import discover_skills
    skills = discover_skills("/tmp/nonexistent-skills-dir-xyz")
    assert skills == []


def test_requires_bins_filtering(tmp_skills_dir):
    """Skills requiring missing binaries should be skipped."""
    from server.skills import discover_skills
    content = """---
name: needs-docker
description: Requires docker
metadata:
  openclaw:
    requires:
      bins: ["docker_unlikely_binary_xyz"]
---

# Needs Docker
"""
    _make_skill(tmp_skills_dir, "needs-docker", content)
    skills = discover_skills(str(tmp_skills_dir))
    assert len(skills) == 0


def test_build_skills_catalog():
    """build_skills_catalog should produce a one-line summary."""
    from server.skills import build_skills_catalog
    skills = [
        {"name": "weather", "description": "Weather lookup"},
        {"name": "food-order", "description": "Order food"},
    ]
    catalog = build_skills_catalog(skills)
    assert "weather" in catalog
    assert "food-order" in catalog
    assert "Weather lookup" in catalog


def test_build_skills_context_no_match():
    """build_skills_context with no keyword match returns catalog only."""
    from server.skills import build_skills_context
    skills = [
        {"name": "weather", "description": "Weather lookup", "body": "# Weather\nLook up weather.", "agent_affinity": None, "requires": {}},
    ]
    context = build_skills_context(skills, "tell me a joke")
    assert "Available skills:" in context
    assert "# Weather" not in context


def test_build_skills_context_keyword_match():
    """build_skills_context with keyword match includes full body."""
    from server.skills import build_skills_context
    skills = [
        {"name": "weather", "description": "Weather lookup", "body": "# Weather\nLook up weather.", "agent_affinity": None, "requires": {}},
    ]
    context = build_skills_context(skills, "what's the weather today")
    assert "# Weather" in context
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/joseph/Projects/conduit && python -m pytest server/tests/test_skills.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.skills'`

**Step 3: Implement `server/skills.py`**

Create `server/skills.py`:

```python
"""Markdown skill discovery, parsing, and context injection.

Skills are OpenClaw-compatible SKILL.md files with YAML frontmatter.
They live in a configurable directory (default: ~/.conduit/skills/).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path

import yaml

log = logging.getLogger("conduit.skills")

# In-memory skill store (populated at startup)
_skills: list[dict] = []


def parse_skill_md(content: str, folder_name: str) -> dict | None:
    """Parse a SKILL.md file into a skill dict.

    Returns None if the file has no valid YAML frontmatter.
    """
    # Extract YAML frontmatter between --- delimiters
    match = re.match(r"^---\s*\n(.+?)\n---\s*\n(.*)", content, re.DOTALL)
    if not match:
        return None

    try:
        frontmatter = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return None

    if not isinstance(frontmatter, dict):
        return None

    body = match.group(2).strip()
    metadata = frontmatter.get("metadata", {})
    conduit_meta = metadata.get("conduit", {})
    openclaw_meta = metadata.get("openclaw", {})

    # Merge requires from both openclaw and conduit metadata
    requires = openclaw_meta.get("requires", {})

    return {
        "name": frontmatter.get("name", folder_name),
        "description": frontmatter.get("description", ""),
        "body": body,
        "agent_affinity": conduit_meta.get("agent", None),
        "requires": requires,
    }


def _check_bins(requires: dict) -> bool:
    """Check that required binaries are available on PATH."""
    bins = requires.get("bins", [])
    for b in bins:
        if not shutil.which(b):
            return False
    return True


def discover_skills(skills_dir: str) -> list[dict]:
    """Scan a directory for valid SKILL.md files and return parsed skills.

    Skips skills whose required binaries are not on PATH.
    Returns empty list if directory doesn't exist.
    """
    expanded = os.path.expanduser(skills_dir)
    base = Path(expanded)

    if not base.is_dir():
        log.debug("Skills directory does not exist: %s", expanded)
        return []

    skills = []
    for skill_dir in sorted(base.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        try:
            content = skill_md.read_text(encoding="utf-8")
        except Exception as e:
            log.warning("Failed to read %s: %s", skill_md, e)
            continue

        skill = parse_skill_md(content, skill_dir.name)
        if skill is None:
            log.debug("Skipping invalid skill: %s", skill_dir.name)
            continue

        if not _check_bins(skill.get("requires", {})):
            log.info("Skipping skill '%s' — missing required binaries", skill["name"])
            continue

        skills.append(skill)
        log.info("Loaded skill: %s", skill["name"])

    return skills


def build_skills_catalog(skills: list[dict]) -> str:
    """Build a one-line catalog of all loaded skills."""
    if not skills:
        return ""
    entries = [f"{s['name']} ({s['description']})" for s in skills]
    return "Available skills: " + ", ".join(entries)


def build_skills_context(skills: list[dict], user_message: str,
                         max_injected: int = 2) -> str:
    """Build the skills context block for the system prompt.

    Always includes the catalog line. If the user's message matches a skill
    by keyword, includes the full body (up to max_injected skills).
    """
    if not skills:
        return ""

    parts = [build_skills_catalog(skills)]

    # Simple keyword matching: check if skill name appears in the message
    message_lower = user_message.lower()
    injected = 0
    for skill in skills:
        if injected >= max_injected:
            break
        # Match on skill name or words from description
        name_words = skill["name"].replace("-", " ").split()
        if any(w in message_lower for w in name_words):
            parts.append(f"\n--- Skill: {skill['name']} ---\n{skill['body']}")
            injected += 1

    return "\n".join(parts)


def load_skills(skills_dir: str) -> list[dict]:
    """Load skills into the module-level store. Called at startup."""
    global _skills
    _skills = discover_skills(skills_dir)
    log.info("Loaded %d markdown skills from %s", len(_skills), skills_dir)
    return _skills


def get_skills() -> list[dict]:
    """Return the currently loaded skills."""
    return _skills


def get_skills_context(user_message: str, max_per_turn: int = 2) -> str:
    """Get the skills context for the current turn."""
    return build_skills_context(_skills, user_message, max_injected=max_per_turn)
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/joseph/Projects/conduit && python -m pytest server/tests/test_skills.py -v`
Expected: PASS (10 tests)

**Step 5: Commit**

```bash
git add server/skills.py server/tests/test_skills.py
git commit -m "feat: add markdown skill discovery and parsing (OpenClaw compatible)"
```

---

## Task 3: Wire Skills into Startup & System Prompt

**Files:**
- Modify: `server/config.yaml:7-19` (add `{skills_context}` placeholder)
- Modify: `server/app.py:223-295` (load skills at startup)
- Modify: `server/app.py:130-219` (inject skills context into prompt)
- Test: `server/tests/test_skills_integration.py`

**Step 1: Write the failing test**

Create `server/tests/test_skills_integration.py`:

```python
"""Integration tests: skills loaded at startup, injected into prompt."""


def test_skills_context_placeholder_in_system_prompt():
    """System prompt template should contain {skills_context} placeholder."""
    from server import config
    assert "{skills_context}" in config.SYSTEM_PROMPT_TEMPLATE


def test_get_skills_returns_list():
    """get_skills should return a list (even if empty)."""
    from server.skills import get_skills
    result = get_skills()
    assert isinstance(result, list)
```

**Step 2: Run test to verify it fails**

Run: `cd /home/joseph/Projects/conduit && python -m pytest server/tests/test_skills_integration.py -v`
Expected: FAIL on `test_skills_context_placeholder_in_system_prompt` — `{skills_context}` not in template

**Step 3: Add `{skills_context}` to system prompt template**

In `server/config.yaml`, modify the `personality.system_prompt` template. After the `{tools_context}` line (line 17), add:

```yaml
    {skills_context}
```

So lines 7-20 become:

```yaml
  system_prompt: |
    You are {name}, a personal AI assistant for Joseph. You are concise, practical, and friendly.
    You speak naturally — never reveal your underlying model or provider. You are {name}, period.

    Current time: {time} on {date} ({day}).

    {memories}

    {pending_tasks}

    {tools_context}

    {skills_context}

    {scout_context}
```

**Step 4: Wire skills loading into `app.py` lifespan**

In `server/app.py`, after tool registration (after line 295 `log.info("Tools registered: ...")`), add:

```python
    # Load markdown skills
    if config.MARKDOWN_SKILLS_ENABLED:
        try:
            from .skills import load_skills
            load_skills(config.MARKDOWN_SKILLS_DIR)
        except Exception as e:
            log.warning("Markdown skills not available: %s", e)
```

**Step 5: Wire skills context into `render_system_prompt_async`**

In `server/app.py`, in the `render_system_prompt_async` function, before the `prompt = template.format(...)` call (around line 210), add:

```python
    # Build skills context
    skills_context = ""
    if config.MARKDOWN_SKILLS_ENABLED:
        try:
            from .skills import get_skills_context
            skills_context = get_skills_context(query, config.MARKDOWN_SKILLS_MAX_PER_TURN)
        except Exception:
            pass
```

And add `skills_context=skills_context` to the `template.format(...)` call (line 210-219). The format call becomes:

```python
    prompt = template.format(
        name=config.PERSONALITY_NAME,
        time=now.strftime("%I:%M %p"),
        date=now.strftime("%B %d, %Y"),
        day=now.strftime("%A"),
        memories=memory_context,
        pending_tasks=pending,
        tools_context=tools_context,
        skills_context=skills_context,
        scout_context=scout_context,
    )
```

Also update the sync `_render_system_prompt_sync` function (around line 107-127) — add `skills_context=""` to its `.format()` call too.

**Step 6: Run tests to verify they pass**

Run: `cd /home/joseph/Projects/conduit && python -m pytest server/tests/test_skills_integration.py -v`
Expected: PASS (2 tests)

**Step 7: Commit**

```bash
git add server/config.yaml server/app.py server/tests/test_skills_integration.py
git commit -m "feat: wire markdown skills into startup and system prompt"
```

---

## Task 4: Plugin SDK (`server/plugins.py` — PluginAPI & Hook Bus)

**Files:**
- Create: `server/plugins.py`
- Test: `server/tests/test_plugins.py`

**Step 1: Write the failing tests**

Create `server/tests/test_plugins.py`:

```python
"""Tests for the Python plugin system."""

import json
from pathlib import Path


def _make_plugin(plugins_dir: Path, plugin_id: str, manifest: dict, init_code: str):
    """Helper: create a plugin directory with plugin.json and __init__.py."""
    plugin_dir = plugins_dir / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.json").write_text(json.dumps(manifest))
    (plugin_dir / "__init__.py").write_text(init_code)


def test_plugin_api_register_tool():
    """PluginAPI.register_tool should store tool in internal list."""
    from server.plugins import PluginAPI

    api = PluginAPI(id="test", config={})

    async def my_handler(x: str) -> str:
        return x

    api.register_tool(
        name="echo",
        description="Echo input",
        parameters={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
        handler=my_handler,
    )
    assert len(api._tools) == 1
    assert api._tools[0].name == "echo"


def test_plugin_api_register_hook():
    """PluginAPI.register_hook should store handler in internal list."""
    from server.plugins import PluginAPI

    api = PluginAPI(id="test", config={})

    async def on_start():
        pass

    api.register_hook("on_startup", on_start)
    assert len(api._hooks) == 1
    assert api._hooks[0] == ("on_startup", on_start)


def test_plugin_api_register_skill():
    """PluginAPI.register_skill should store skill in internal list."""
    from server.plugins import PluginAPI

    api = PluginAPI(id="test", config={})
    api.register_skill("test-skill", "A test skill", "# Test\nDo the thing.")
    assert len(api._registered_skills) == 1
    assert api._registered_skills[0]["name"] == "test-skill"


def test_discover_plugins(tmp_plugins_dir, sample_plugin_manifest):
    """discover_plugins should find plugins with valid plugin.json."""
    init_code = '''
from server.plugins import PluginAPI

async def _handler(msg: str) -> str:
    return f"echo: {msg}"

def register(api: PluginAPI):
    api.register_tool(
        name="test_echo",
        description="Echo a message",
        parameters={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
        handler=_handler,
    )
'''
    _make_plugin(tmp_plugins_dir, "test-plugin", sample_plugin_manifest, init_code)
    from server.plugins import discover_plugins
    plugins = discover_plugins(str(tmp_plugins_dir))
    assert len(plugins) == 1
    assert plugins[0]["id"] == "test-plugin"


def test_discover_plugins_missing_manifest(tmp_plugins_dir):
    """Directories without plugin.json should be skipped."""
    (tmp_plugins_dir / "no-manifest").mkdir()
    (tmp_plugins_dir / "no-manifest" / "__init__.py").write_text("# empty")
    from server.plugins import discover_plugins
    plugins = discover_plugins(str(tmp_plugins_dir))
    assert len(plugins) == 0


def test_discover_plugins_missing_dir():
    """Nonexistent directory should return empty list."""
    from server.plugins import discover_plugins
    plugins = discover_plugins("/tmp/nonexistent-plugins-dir-xyz")
    assert len(plugins) == 0


def test_load_plugin_registers_tools(tmp_plugins_dir, sample_plugin_manifest):
    """load_plugin should call register() and collect tools."""
    init_code = '''
from server.plugins import PluginAPI

async def _handler(msg: str) -> str:
    return f"echo: {msg}"

def register(api: PluginAPI):
    api.register_tool(
        name="test_echo",
        description="Echo a message",
        parameters={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
        handler=_handler,
    )
'''
    _make_plugin(tmp_plugins_dir, "test-plugin", sample_plugin_manifest, init_code)
    from server.plugins import load_plugin
    result = load_plugin(tmp_plugins_dir / "test-plugin")
    assert result is not None
    tools, hooks, skills = result
    assert len(tools) == 1
    assert tools[0].name == "test_echo"


def test_load_plugin_bad_init(tmp_plugins_dir, sample_plugin_manifest):
    """Plugins with syntax errors should return None (graceful failure)."""
    _make_plugin(tmp_plugins_dir, "bad-plugin", sample_plugin_manifest, "def register(api):\n    raise RuntimeError('boom')")
    from server.plugins import load_plugin
    result = load_plugin(tmp_plugins_dir / "bad-plugin")
    assert result is None


import pytest

@pytest.mark.asyncio
async def test_dispatch_hook():
    """dispatch_hook should call all registered handlers for an event."""
    from server.plugins import dispatch_hook, _hooks

    calls = []

    async def handler1(**kwargs):
        calls.append(("h1", kwargs))

    async def handler2(**kwargs):
        calls.append(("h2", kwargs))
        return {"system_prompt": "modified"}

    _hooks.clear()
    _hooks["before_agent_start"] = [handler1, handler2]

    result = await dispatch_hook("before_agent_start", messages=[], system_prompt="orig")
    assert len(calls) == 2
    assert result == {"system_prompt": "modified"}
    _hooks.clear()


@pytest.mark.asyncio
async def test_dispatch_hook_no_handlers():
    """dispatch_hook with no handlers should return None."""
    from server.plugins import dispatch_hook, _hooks
    _hooks.clear()
    result = await dispatch_hook("nonexistent_event")
    assert result is None
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/joseph/Projects/conduit && python -m pytest server/tests/test_plugins.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.plugins'`

**Step 3: Implement `server/plugins.py`**

Create `server/plugins.py`:

```python
"""Python plugin system — discovery, loading, PluginAPI, and hook event bus.

Plugins are Python packages in a configurable directory (default: ~/.conduit/plugins/).
Each plugin has a plugin.json manifest and an __init__.py that exports register(api).
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from .tools.definitions import ToolDefinition

log = logging.getLogger("conduit.plugins")

# Global hook event bus: event_name -> list of async handlers
_hooks: dict[str, list[Callable]] = {}

# Loaded plugin metadata
_loaded_plugins: list[dict] = []


@dataclass
class PluginAPI:
    """API object passed to plugin register() functions."""

    id: str
    config: dict

    # Internal collectors — populated during register()
    _tools: list[ToolDefinition] = field(default_factory=list)
    _hooks: list[tuple[str, Callable]] = field(default_factory=list)
    _registered_skills: list[dict] = field(default_factory=list)

    def register_tool(self, name: str, description: str, parameters: dict,
                      handler: Callable[..., Awaitable[str]],
                      permission: str = "none") -> None:
        """Register a tool into Conduit's global tool registry."""
        tool = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            permission=permission,
        )
        self._tools.append(tool)
        log.debug("Plugin '%s' registered tool: %s", self.id, name)

    def register_hook(self, event: str, handler: Callable) -> None:
        """Register a lifecycle hook handler."""
        self._hooks.append((event, handler))
        log.debug("Plugin '%s' registered hook: %s", self.id, event)

    def register_skill(self, name: str, description: str, content: str) -> None:
        """Programmatically register a skill (alternative to SKILL.md)."""
        self._registered_skills.append({
            "name": name,
            "description": description,
            "body": content,
            "agent_affinity": None,
            "requires": {},
        })
        log.debug("Plugin '%s' registered skill: %s", self.id, name)

    def log(self, level: str, message: str) -> None:
        """Log a message under the plugin's namespace."""
        getattr(log, level.lower(), log.info)(
            "[plugin:%s] %s", self.id, message
        )


def discover_plugins(plugins_dir: str) -> list[dict]:
    """Scan a directory for plugin directories containing plugin.json.

    Returns list of manifest dicts (with added '_path' key).
    Does NOT load/import them — use load_plugin() for that.
    """
    expanded = os.path.expanduser(plugins_dir)
    base = Path(expanded)

    if not base.is_dir():
        log.debug("Plugins directory does not exist: %s", expanded)
        return []

    plugins = []
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        manifest_path = entry / "plugin.json"
        if not manifest_path.exists():
            continue

        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Bad plugin.json in %s: %s", entry.name, e)
            continue

        if "id" not in manifest:
            log.warning("Plugin %s missing 'id' in manifest — skipping", entry.name)
            continue

        manifest["_path"] = str(entry)
        plugins.append(manifest)

    return plugins


def load_plugin(plugin_path: Path) -> tuple[list[ToolDefinition], list[tuple[str, Callable]], list[dict]] | None:
    """Load a single plugin by importing its __init__.py and calling register().

    Returns (tools, hooks, skills) or None on failure.
    """
    manifest_path = plugin_path / "plugin.json"
    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except Exception as e:
        log.warning("Cannot read manifest for %s: %s", plugin_path.name, e)
        return None

    plugin_id = manifest.get("id", plugin_path.name)

    # Resolve plugin config from config_schema if present
    plugin_config = {}
    schema = manifest.get("config_schema", {})
    for key, spec in schema.items():
        if spec.get("type") == "string" and key.endswith("_env"):
            plugin_config[key] = os.getenv(spec.get("default", key), "")

    api = PluginAPI(id=plugin_id, config=plugin_config)

    # Temporarily add plugin directory to sys.path for import
    plugin_dir_str = str(plugin_path)
    sys.path.insert(0, plugin_dir_str)
    try:
        # Import the plugin module
        module_name = f"_conduit_plugin_{plugin_id.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(
            module_name, plugin_path / "__init__.py"
        )
        if spec is None or spec.loader is None:
            log.warning("Cannot create module spec for plugin '%s'", plugin_id)
            return None

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        # Call register()
        register_fn = getattr(module, "register", None)
        if register_fn is None:
            log.warning("Plugin '%s' has no register() function", plugin_id)
            return None

        register_fn(api)

    except Exception as e:
        log.warning("Plugin '%s' failed to load: %s", plugin_id, e)
        return None
    finally:
        if plugin_dir_str in sys.path:
            sys.path.remove(plugin_dir_str)

    return api._tools, api._hooks, api._registered_skills


def load_all_plugins(plugins_dir: str) -> tuple[list[ToolDefinition], list[dict]]:
    """Discover and load all plugins. Returns (all_tools, all_skills).

    Registers hooks into the global _hooks bus.
    """
    global _loaded_plugins

    manifests = discover_plugins(plugins_dir)
    all_tools: list[ToolDefinition] = []
    all_skills: list[dict] = []

    for manifest in manifests:
        plugin_path = Path(manifest["_path"])
        result = load_plugin(plugin_path)
        if result is None:
            continue

        tools, hooks, skills = result

        # Register tools
        all_tools.extend(tools)

        # Register hooks into global bus
        for event, handler in hooks:
            if event not in _hooks:
                _hooks[event] = []
            _hooks[event].append(handler)

        # Collect skills
        all_skills.extend(skills)

        _loaded_plugins.append(manifest)
        log.info("Loaded plugin: %s (%d tools, %d hooks, %d skills)",
                 manifest["id"], len(tools), len(hooks), len(skills))

    return all_tools, all_skills


async def dispatch_hook(event: str, **kwargs) -> dict | None:
    """Run all handlers for a hook event. Returns merged modifications or None."""
    handlers = _hooks.get(event, [])
    if not handlers:
        return None

    merged: dict = {}
    for handler in handlers:
        try:
            result = await handler(**kwargs)
            if isinstance(result, dict):
                merged.update(result)
        except Exception as e:
            log.warning("Hook handler failed for '%s': %s", event, e)

    return merged if merged else None


def get_loaded_plugins() -> list[dict]:
    """Return metadata for all loaded plugins."""
    return _loaded_plugins
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/joseph/Projects/conduit && pip install pytest-asyncio 2>/dev/null; python -m pytest server/tests/test_plugins.py -v`
Expected: PASS (10 tests)

Note: `pytest-asyncio` is needed for `@pytest.mark.asyncio` tests. It's pure Python, available via pip on both local and tablet.

**Step 5: Commit**

```bash
git add server/plugins.py server/tests/test_plugins.py
git commit -m "feat: add Python plugin system with PluginAPI and hook event bus"
```

---

## Task 5: Wire Plugins into Startup & Agent Loop

**Files:**
- Modify: `server/app.py:223-295` (load plugins at startup, register tools)
- Modify: `server/agent.py:29-60` (dispatch after_tool_call hook)
- Modify: `server/agent.py:63-96` (dispatch before_agent_start hook)
- Test: `server/tests/test_plugins_integration.py`

**Step 1: Write the failing test**

Create `server/tests/test_plugins_integration.py`:

```python
"""Integration tests for plugin loading at startup and hook dispatch."""

import pytest


@pytest.mark.asyncio
async def test_dispatch_hook_before_agent_start():
    """before_agent_start hook can modify system prompt."""
    from server.plugins import dispatch_hook, _hooks

    async def modifier(**kwargs):
        return {"system_prompt": kwargs.get("system_prompt", "") + "\nPlugin injected."}

    _hooks.clear()
    _hooks["before_agent_start"] = [modifier]
    result = await dispatch_hook("before_agent_start", messages=[], system_prompt="Base prompt.")
    assert result is not None
    assert "Plugin injected." in result["system_prompt"]
    _hooks.clear()


@pytest.mark.asyncio
async def test_dispatch_hook_after_tool_call():
    """after_tool_call hook can transform tool results."""
    from server.plugins import dispatch_hook, _hooks

    async def transformer(**kwargs):
        return {"result": kwargs.get("result", "") + " [enriched]"}

    _hooks.clear()
    _hooks["after_tool_call"] = [transformer]
    result = await dispatch_hook("after_tool_call", tool_name="web_search", args={}, result="raw result")
    assert result is not None
    assert "[enriched]" in result["result"]
    _hooks.clear()
```

**Step 2: Run tests to verify they fail (should pass since dispatch_hook exists)**

Run: `cd /home/joseph/Projects/conduit && python -m pytest server/tests/test_plugins_integration.py -v`
Expected: PASS (these test the existing dispatch_hook function)

**Step 3: Wire plugin loading into `app.py` lifespan**

In `server/app.py`, after the markdown skills loading block (added in Task 3), add:

```python
    # Load Python plugins
    if config.PLUGINS_ENABLED:
        try:
            from .plugins import load_all_plugins
            from .tools import register as register_tool
            from .skills import _skills

            plugin_tools, plugin_skills = load_all_plugins(config.PLUGINS_DIR)
            for tool in plugin_tools:
                register_tool(tool)
            # Merge plugin-registered skills into the skills store
            _skills.extend(plugin_skills)
            if plugin_tools:
                log.info("Plugin tools registered: %s", [t.name for t in plugin_tools])
            if plugin_skills:
                log.info("Plugin skills registered: %s", [s["name"] for s in plugin_skills])
        except Exception as e:
            log.warning("Plugins not available: %s", e)
```

**Step 4: Wire hook dispatch into `agent.py`**

In `server/agent.py`, in the `run_agent_loop` function:

**before_agent_start hook** — add before the while loop (after line 94 `turns = 0`):

```python
    # Dispatch before_agent_start hook
    try:
        from .plugins import dispatch_hook
        hook_result = await dispatch_hook(
            "before_agent_start", messages=messages, system_prompt=system
        )
        if hook_result and "system_prompt" in hook_result:
            system = hook_result["system_prompt"]
    except Exception:
        pass
```

**after_tool_call hook** — add inside the tool execution loop, after `result = await _execute_tool(tool, tc, ws, manager)` (line 132):

```python
                # Dispatch after_tool_call hook
                try:
                    from .plugins import dispatch_hook as _dispatch_hook
                    hook_result = await _dispatch_hook(
                        "after_tool_call", tool_name=tc.name,
                        args=tc.arguments, result=result
                    )
                    if hook_result and "result" in hook_result:
                        result = hook_result["result"]
                except Exception:
                    pass
```

**Step 5: Run all tests to verify they pass**

Run: `cd /home/joseph/Projects/conduit && python -m pytest server/tests/ -v`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add server/app.py server/agent.py server/tests/test_plugins_integration.py
git commit -m "feat: wire plugins into startup (tool registration) and agent loop (hooks)"
```

---

## Task 6: Subagent Session Model & Registry (`server/subagents.py` — Part 1)

**Files:**
- Create: `server/subagents.py`
- Test: `server/tests/test_subagents.py`

**Step 1: Write the failing tests**

Create `server/tests/test_subagents.py`:

```python
"""Tests for the subagent session system — data model and registry."""

import time

import pytest


def test_subagent_session_creation():
    """SubagentSession should be creatable with required fields."""
    from server.subagents import SubagentSession
    session = SubagentSession(
        run_id="abc123def456",
        child_agent_id="researcher",
        parent_session_key="websocket:main:conv1",
        parent_agent_id="default",
        task="Find info about X",
    )
    assert session.run_id == "abc123def456"
    assert session.status == "running"
    assert session.depth == 0
    assert session.messages == []
    assert session.result is None
    assert session.timeout_seconds == 300


def test_subagent_session_to_dict():
    """SubagentSession.to_dict should produce a JSON-serializable dict."""
    import json
    from server.subagents import SubagentSession
    session = SubagentSession(
        run_id="abc123",
        child_agent_id="researcher",
        parent_session_key="ws:main:c1",
        parent_agent_id="default",
        task="test",
    )
    d = session.to_dict()
    # Should be JSON-serializable
    serialized = json.dumps(d)
    assert "abc123" in serialized
    assert d["status"] == "running"


def test_registry_create_session():
    """SessionRegistry should create and track sessions."""
    from server.subagents import SessionRegistry
    registry = SessionRegistry()
    session = registry.create_session(
        child_agent_id="researcher",
        parent_session_key="ws:main:c1",
        parent_agent_id="default",
        task="research X",
        parent_depth=0,
    )
    assert session.run_id in registry._sessions
    assert session.depth == 1
    assert session.status == "running"


def test_registry_depth_limit():
    """SessionRegistry should reject sessions exceeding max depth."""
    from server.subagents import SessionRegistry
    registry = SessionRegistry(max_spawn_depth=2)
    # depth 0 parent -> depth 1 child: OK
    s1 = registry.create_session("r", "ws:m:c1", "default", "t1", parent_depth=0)
    assert s1 is not None
    # depth 1 parent -> depth 2 child: OK (depth 2 == max, still allowed)
    s2 = registry.create_session("r", "ws:m:c1", "default", "t2", parent_depth=1)
    assert s2 is not None
    # depth 2 parent -> depth 3 child: REJECTED
    s3 = registry.create_session("r", "ws:m:c1", "default", "t3", parent_depth=2)
    assert s3 is None


def test_registry_children_limit():
    """SessionRegistry should reject sessions exceeding max children per parent."""
    from server.subagents import SessionRegistry
    registry = SessionRegistry(max_children=2)
    registry.create_session("r", "ws:m:c1", "default", "t1", parent_depth=0)
    registry.create_session("r", "ws:m:c1", "default", "t2", parent_depth=0)
    # Third child for same parent: REJECTED
    s3 = registry.create_session("r", "ws:m:c1", "default", "t3", parent_depth=0)
    assert s3 is None


def test_registry_get_and_list():
    """SessionRegistry get/list operations."""
    from server.subagents import SessionRegistry
    registry = SessionRegistry()
    s = registry.create_session("r", "ws:m:c1", "default", "t1", parent_depth=0)
    assert registry.get(s.run_id) is s
    assert registry.get("nonexistent") is None
    sessions = registry.list_sessions()
    assert len(sessions) == 1


def test_registry_list_by_status():
    """SessionRegistry should filter by status."""
    from server.subagents import SessionRegistry
    registry = SessionRegistry()
    s1 = registry.create_session("r", "ws:m:c1", "default", "t1", parent_depth=0)
    s2 = registry.create_session("r", "ws:m:c1", "default", "t2", parent_depth=0)
    s1.status = "done"
    running = registry.list_sessions(status_filter="running")
    assert len(running) == 1
    assert running[0].run_id == s2.run_id


def test_registry_active_children_count():
    """active_children_count should count running sessions for a parent."""
    from server.subagents import SessionRegistry
    registry = SessionRegistry()
    s1 = registry.create_session("r", "ws:m:c1", "default", "t1", parent_depth=0)
    s2 = registry.create_session("r", "ws:m:c1", "default", "t2", parent_depth=0)
    assert registry.active_children_count("ws:m:c1") == 2
    s1.status = "done"
    assert registry.active_children_count("ws:m:c1") == 1
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/joseph/Projects/conduit && python -m pytest server/tests/test_subagents.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server.subagents'`

**Step 3: Implement `server/subagents.py` (Part 1 — data model + registry)**

Create `server/subagents.py`:

```python
"""Subagent session system — isolated session spawning with lifecycle management.

Sessions are tracked in memory with optional SQLite persistence for completed
sessions. Each session runs an agent loop in an isolated message context.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

log = logging.getLogger("conduit.subagents")

if TYPE_CHECKING:
    from .agents import AgentRegistry


@dataclass
class SubagentSession:
    """An isolated subagent session."""

    run_id: str
    child_agent_id: str
    parent_session_key: str
    parent_agent_id: str
    task: str
    label: str | None = None
    model_override: str | None = None
    messages: list[dict] = field(default_factory=list)
    status: str = "running"  # "running" | "done" | "error" | "timeout"
    result: str | None = None
    depth: int = 0
    children: list[str] = field(default_factory=list)
    cleanup: str = "keep"  # "delete" | "keep"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    ended_at: float | None = None
    timeout_seconds: int = 300
    _task: asyncio.Task | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict (excludes asyncio.Task)."""
        return {
            "run_id": self.run_id,
            "child_agent_id": self.child_agent_id,
            "parent_session_key": self.parent_session_key,
            "parent_agent_id": self.parent_agent_id,
            "task": self.task,
            "label": self.label,
            "model_override": self.model_override,
            "messages": self.messages,
            "status": self.status,
            "result": self.result,
            "depth": self.depth,
            "children": self.children,
            "cleanup": self.cleanup,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "timeout_seconds": self.timeout_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SubagentSession:
        """Deserialize from a dict."""
        return cls(
            run_id=data["run_id"],
            child_agent_id=data["child_agent_id"],
            parent_session_key=data["parent_session_key"],
            parent_agent_id=data["parent_agent_id"],
            task=data["task"],
            label=data.get("label"),
            model_override=data.get("model_override"),
            messages=data.get("messages", []),
            status=data.get("status", "running"),
            result=data.get("result"),
            depth=data.get("depth", 0),
            children=data.get("children", []),
            cleanup=data.get("cleanup", "keep"),
            created_at=data.get("created_at", time.time()),
            started_at=data.get("started_at"),
            ended_at=data.get("ended_at"),
            timeout_seconds=data.get("timeout_seconds", 300),
        )


class SessionRegistry:
    """In-memory registry of subagent sessions."""

    def __init__(self, max_spawn_depth: int = 2, max_children: int = 5,
                 default_timeout: int = 300):
        self._sessions: dict[str, SubagentSession] = {}
        self.max_spawn_depth = max_spawn_depth
        self.max_children = max_children
        self.default_timeout = default_timeout

    def create_session(
        self,
        child_agent_id: str,
        parent_session_key: str,
        parent_agent_id: str,
        task: str,
        parent_depth: int,
        label: str | None = None,
        model_override: str | None = None,
        timeout: int | None = None,
        cleanup: str = "keep",
    ) -> SubagentSession | None:
        """Create a new session. Returns None if limits exceeded."""
        child_depth = parent_depth + 1
        if child_depth > self.max_spawn_depth:
            log.warning("Depth limit reached: %d > %d", child_depth, self.max_spawn_depth)
            return None

        active = self.active_children_count(parent_session_key)
        if active >= self.max_children:
            log.warning("Children limit reached for %s: %d >= %d",
                        parent_session_key, active, self.max_children)
            return None

        run_id = uuid.uuid4().hex[:12]
        session = SubagentSession(
            run_id=run_id,
            child_agent_id=child_agent_id,
            parent_session_key=parent_session_key,
            parent_agent_id=parent_agent_id,
            task=task,
            label=label or f"{child_agent_id}:{run_id[:6]}",
            model_override=model_override,
            depth=child_depth,
            cleanup=cleanup,
            timeout_seconds=timeout or self.default_timeout,
        )
        self._sessions[run_id] = session
        log.info("Created session %s: %s -> %s (depth=%d)",
                 run_id, parent_agent_id, child_agent_id, child_depth)
        return session

    def get(self, run_id: str) -> SubagentSession | None:
        """Get a session by run_id."""
        return self._sessions.get(run_id)

    def get_by_label(self, label: str) -> SubagentSession | None:
        """Get a session by label (first match)."""
        for session in self._sessions.values():
            if session.label == label:
                return session
        return None

    def list_sessions(self, status_filter: str | None = None) -> list[SubagentSession]:
        """List sessions, optionally filtered by status."""
        sessions = list(self._sessions.values())
        if status_filter:
            sessions = [s for s in sessions if s.status == status_filter]
        return sessions

    def active_children_count(self, parent_session_key: str) -> int:
        """Count running child sessions for a parent."""
        return sum(
            1 for s in self._sessions.values()
            if s.parent_session_key == parent_session_key and s.status == "running"
        )

    def mark_complete(self, run_id: str, status: str, result: str | None) -> None:
        """Mark a session as complete (done/error/timeout)."""
        session = self._sessions.get(run_id)
        if session:
            session.status = status
            session.result = result
            session.ended_at = time.time()

    def remove(self, run_id: str) -> None:
        """Remove a session from the in-memory registry."""
        self._sessions.pop(run_id, None)


# ---------------------------------------------------------------------------
# SQLite persistence (for completed sessions)
# ---------------------------------------------------------------------------

_db_path: str | None = None


async def init_db(db_path: str = "~/.conduit/subagents.db") -> None:
    """Initialize the SQLite database for session persistence."""
    global _db_path
    import os
    expanded = os.path.expanduser(db_path)
    os.makedirs(os.path.dirname(expanded), exist_ok=True)
    _db_path = expanded

    try:
        import aiosqlite
        async with aiosqlite.connect(_db_path) as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS subagent_sessions (
                    run_id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    ended_at REAL
                )
            """)
            await conn.commit()
        log.info("Subagent session DB initialized at %s", _db_path)
    except ImportError:
        log.warning("aiosqlite not available — subagent sessions will not persist")
        _db_path = None
    except Exception as e:
        log.warning("Subagent DB init failed: %s", e)
        _db_path = None


async def persist_session(session: SubagentSession) -> None:
    """Write a completed session to SQLite."""
    if not _db_path:
        return
    try:
        import aiosqlite
        async with aiosqlite.connect(_db_path) as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO subagent_sessions (run_id, data, created_at, ended_at) "
                "VALUES (?, ?, ?, ?)",
                (session.run_id, json.dumps(session.to_dict()),
                 session.created_at, session.ended_at),
            )
            await conn.commit()
    except Exception as e:
        log.warning("Failed to persist session %s: %s", session.run_id, e)


# ---------------------------------------------------------------------------
# Announcement queue
# ---------------------------------------------------------------------------

_pending_announces: dict[str, list[dict]] = {}


def queue_announcement(parent_session_key: str, announcement: dict) -> None:
    """Queue a completion announcement for a parent session."""
    if parent_session_key not in _pending_announces:
        _pending_announces[parent_session_key] = []
    _pending_announces[parent_session_key].append(announcement)


def drain_announcements(parent_session_key: str) -> list[dict]:
    """Drain and return all pending announcements for a parent."""
    return _pending_announces.pop(parent_session_key, [])
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/joseph/Projects/conduit && python -m pytest server/tests/test_subagents.py -v`
Expected: PASS (9 tests)

**Step 5: Commit**

```bash
git add server/subagents.py server/tests/test_subagents.py
git commit -m "feat: add subagent session model, registry, and SQLite persistence"
```

---

## Task 7: Subagent Execution & Session Tools (`server/subagents.py` — Part 2)

**Files:**
- Modify: `server/subagents.py` (add spawn/execution logic and tool builders)
- Test: `server/tests/test_subagent_tools.py`

**Step 1: Write the failing tests**

Create `server/tests/test_subagent_tools.py`:

```python
"""Tests for subagent session tools (spawn, list, history, kill)."""

import pytest


def test_build_session_tools_returns_five():
    """build_session_tools should return 5 ToolDefinitions."""
    from server.subagents import build_session_tools, SessionRegistry
    registry = SessionRegistry()
    tools = build_session_tools(registry, "default", "ws:main:c1", depth=0)
    assert len(tools) == 5
    names = {t.name for t in tools}
    assert names == {"sessions_spawn", "sessions_send", "sessions_list",
                     "sessions_history", "sessions_kill"}


def test_build_session_tools_none_at_max_depth():
    """At max depth, sessions_spawn should not be available."""
    from server.subagents import build_session_tools, SessionRegistry
    registry = SessionRegistry(max_spawn_depth=1)
    tools = build_session_tools(registry, "default", "ws:main:c1", depth=1)
    names = {t.name for t in tools}
    # spawn should be excluded at max depth
    assert "sessions_spawn" not in names
    # Other tools should still be present
    assert "sessions_list" in names


@pytest.mark.asyncio
async def test_sessions_list_tool():
    """sessions_list tool should return session info."""
    from server.subagents import build_session_tools, SessionRegistry
    registry = SessionRegistry()
    registry.create_session("researcher", "ws:m:c1", "default", "Find X", parent_depth=0)

    tools = build_session_tools(registry, "default", "ws:m:c1", depth=0)
    list_tool = next(t for t in tools if t.name == "sessions_list")
    result = await list_tool.handler()
    assert "researcher" in result
    assert "running" in result


@pytest.mark.asyncio
async def test_sessions_kill_tool():
    """sessions_kill should mark session as error."""
    from server.subagents import build_session_tools, SessionRegistry
    registry = SessionRegistry()
    s = registry.create_session("researcher", "ws:m:c1", "default", "Find X", parent_depth=0)

    tools = build_session_tools(registry, "default", "ws:m:c1", depth=0)
    kill_tool = next(t for t in tools if t.name == "sessions_kill")
    result = await kill_tool.handler(run_id=s.run_id)
    assert "Cancelled" in result or "cancelled" in result.lower()
    assert registry.get(s.run_id).status == "error"


@pytest.mark.asyncio
async def test_sessions_history_tool():
    """sessions_history should return conversation history."""
    from server.subagents import build_session_tools, SessionRegistry
    registry = SessionRegistry()
    s = registry.create_session("researcher", "ws:m:c1", "default", "Find X", parent_depth=0)
    s.messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]

    tools = build_session_tools(registry, "default", "ws:m:c1", depth=0)
    history_tool = next(t for t in tools if t.name == "sessions_history")
    result = await history_tool.handler(run_id=s.run_id)
    assert "hello" in result
    assert "hi" in result
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/joseph/Projects/conduit && python -m pytest server/tests/test_subagent_tools.py -v`
Expected: FAIL with `ImportError` (no `build_session_tools` function yet)

**Step 3: Add session tools and spawn logic to `server/subagents.py`**

Append to the end of `server/subagents.py`:

```python
# ---------------------------------------------------------------------------
# Subagent execution
# ---------------------------------------------------------------------------

async def run_subagent(session: SubagentSession, agent_registry: "AgentRegistry") -> None:
    """Execute a subagent session's task in an isolated agent loop."""
    from . import agent as agent_mod
    from . import config
    from .agents import SilentAdapter
    from .app import render_system_prompt_async
    from .tools import get_all as get_all_tools

    session.started_at = time.time()

    target = agent_registry.get(session.child_agent_id)
    if not target:
        session.status = "error"
        session.result = f"Unknown agent: {session.child_agent_id}"
        session.ended_at = time.time()
        return

    # Build system prompt with subagent preamble
    system = await render_system_prompt_async(query=session.task)
    system = target.get_system_prompt(system)
    preamble = (
        f"\n\n--- Subagent Context ---\n"
        f"You are running as a subagent (depth {session.depth}). "
        f"Your task: {session.task}\n"
        f"Focus on this task and report your findings clearly.\n"
        f"--- End Subagent Context ---"
    )
    system += preamble

    # Build tools (including plugin tools, filtered per agent)
    all_tools = get_all_tools()
    tools = target.get_tools(all_tools)

    # If not at max depth, add session tools so this subagent can spawn children
    if session.depth < config.SUBAGENTS_MAX_SPAWN_DEPTH:
        child_tools = build_session_tools(
            _module_registry, session.child_agent_id,
            f"subagent:{session.run_id}", depth=session.depth,
            agent_registry=agent_registry,
        )
        tools.extend(child_tools)

    # Initialize messages
    session.messages = [{"role": "user", "content": session.task}]

    adapter = SilentAdapter()

    try:
        if getattr(target.provider, 'manages_own_tools', False):
            response_text, usage, _, _ = await asyncio.wait_for(
                target.provider.run(
                    prompt=session.task, session_id=None,
                    ws=None, manager=adapter,
                ),
                timeout=session.timeout_seconds,
            )
        elif target.provider.supports_tools and config.TOOLS_ENABLED and tools:
            response_text, usage = await asyncio.wait_for(
                agent_mod.run_agent_loop(
                    session.messages, system, target.provider, tools,
                    None, adapter, max_turns=target.get_max_turns(),
                ),
                timeout=session.timeout_seconds,
            )
        else:
            response_text, usage = await asyncio.wait_for(
                target.provider.generate(session.messages, system=system),
                timeout=session.timeout_seconds,
            )

        result = adapter.get_response() or response_text
        session.status = "done"
        session.result = result or "(no response)"

    except asyncio.TimeoutError:
        session.status = "timeout"
        session.result = f"Timed out after {session.timeout_seconds}s"
    except Exception as e:
        session.status = "error"
        session.result = f"Error: {e}"
        log.error("Subagent %s failed: %s", session.run_id, e, exc_info=True)

    session.ended_at = time.time()

    # Persist to SQLite
    await persist_session(session)

    # Queue announcement for parent
    runtime = session.ended_at - session.created_at
    announcement = {
        "run_id": session.run_id,
        "label": session.label,
        "agent_id": session.child_agent_id,
        "status": session.status,
        "result_summary": (session.result or "")[:500],
        "runtime_seconds": round(runtime, 1),
    }
    queue_announcement(session.parent_session_key, announcement)
    log.info("Subagent %s (%s) completed: %s in %.1fs",
             session.run_id, session.label, session.status, runtime)


# Module-level registry reference (set during startup)
_module_registry: SessionRegistry | None = None


def init_registry(max_spawn_depth: int = 2, max_children: int = 5,
                  default_timeout: int = 300) -> SessionRegistry:
    """Create and store the module-level session registry."""
    global _module_registry
    _module_registry = SessionRegistry(
        max_spawn_depth=max_spawn_depth,
        max_children=max_children,
        default_timeout=default_timeout,
    )
    return _module_registry


def get_registry() -> SessionRegistry | None:
    """Return the module-level session registry."""
    return _module_registry


# ---------------------------------------------------------------------------
# Session tools (injected into agent tool list)
# ---------------------------------------------------------------------------

def build_session_tools(
    registry: SessionRegistry,
    caller_agent_id: str,
    parent_session_key: str,
    depth: int,
    agent_registry: "AgentRegistry | None" = None,
) -> list:
    """Build the 5 session tools for an agent."""
    from .tools.definitions import ToolDefinition

    tools = []

    # --- sessions_spawn ---
    if depth < registry.max_spawn_depth:
        async def sessions_spawn(agent_id: str, task: str,
                                 label: str = "", timeout: int = 0,
                                 cleanup: str = "keep") -> str:
            if agent_registry is None:
                return "Error: agent registry not available"
            target = agent_registry.get(agent_id)
            if not target:
                available = [a["id"] for a in agent_registry.list_agents()]
                return f"Error: unknown agent '{agent_id}'. Available: {', '.join(available)}"

            session = registry.create_session(
                child_agent_id=agent_id,
                parent_session_key=parent_session_key,
                parent_agent_id=caller_agent_id,
                task=task,
                parent_depth=depth,
                label=label or None,
                timeout=timeout or None,
                cleanup=cleanup,
            )
            if session is None:
                return "Error: session limit reached (depth or children)"

            # Launch execution in background
            session._task = asyncio.create_task(
                run_subagent(session, agent_registry)
            )
            return f"Spawned: {session.run_id} (agent: {agent_id}, label: {session.label})"

        tools.append(ToolDefinition(
            name="sessions_spawn",
            description="Spawn a subagent to work on a task in an isolated session. Returns immediately with a run_id.",
            parameters={
                "type": "object",
                "properties": {
                    "agent_id": {"type": "string", "description": "Target agent ID to run the task"},
                    "task": {"type": "string", "description": "The task for the subagent to perform"},
                    "label": {"type": "string", "description": "Human-readable label (optional)"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (0 = default)"},
                    "cleanup": {"type": "string", "enum": ["keep", "delete"], "description": "Whether to keep session after completion"},
                },
                "required": ["agent_id", "task"],
            },
            handler=sessions_spawn,
            permission="none",
        ))

    # --- sessions_send ---
    async def sessions_send(run_id_or_label: str, message: str,
                            timeout: int = 60) -> str:
        session = registry.get(run_id_or_label) or registry.get_by_label(run_id_or_label)
        if not session:
            return f"Error: no session found for '{run_id_or_label}'"
        if session.status != "running":
            return f"Error: session '{run_id_or_label}' is not running (status: {session.status})"
        # Append message and note that inter-session messaging requires
        # the child to be idle (between turns). For now, return status.
        return f"Session {session.run_id} ({session.label}) is {session.status}. Direct messaging to running sessions is not yet supported — use sessions_spawn for new tasks or wait for completion."

    tools.append(ToolDefinition(
        name="sessions_send",
        description="Send a message to a running subagent session by run_id or label.",
        parameters={
            "type": "object",
            "properties": {
                "run_id_or_label": {"type": "string", "description": "Run ID or label of the target session"},
                "message": {"type": "string", "description": "Message to send"},
                "timeout": {"type": "integer", "description": "Response timeout in seconds"},
            },
            "required": ["run_id_or_label", "message"],
        },
        handler=sessions_send,
        permission="none",
    ))

    # --- sessions_list ---
    async def sessions_list(status_filter: str = "") -> str:
        sessions = registry.list_sessions(status_filter=status_filter or None)
        if not sessions:
            return "No sessions found."
        lines = []
        for s in sessions:
            runtime = ""
            if s.started_at:
                elapsed = (s.ended_at or time.time()) - s.started_at
                runtime = f", {elapsed:.1f}s"
            lines.append(
                f"- {s.run_id} [{s.label}] agent={s.child_agent_id} "
                f"status={s.status}{runtime}"
            )
        return "\n".join(lines)

    tools.append(ToolDefinition(
        name="sessions_list",
        description="List all subagent sessions with their status, agent, and runtime.",
        parameters={
            "type": "object",
            "properties": {
                "status_filter": {"type": "string", "description": "Filter by status: running, done, error, timeout (optional)"},
            },
        },
        handler=sessions_list,
        permission="none",
    ))

    # --- sessions_history ---
    async def sessions_history(run_id: str) -> str:
        session = registry.get(run_id)
        if not session:
            return f"Error: no session '{run_id}'"
        if not session.messages:
            return f"Session {run_id}: no messages yet."
        lines = []
        for msg in session.messages:
            role = msg.get("role", "?")
            content = str(msg.get("content", ""))[:500]
            lines.append(f"[{role}] {content}")
        result = "\n".join(lines)
        # Cap at 10k chars
        if len(result) > 10000:
            result = result[:10000] + "\n... (truncated)"
        return result

    tools.append(ToolDefinition(
        name="sessions_history",
        description="Get the conversation history of a subagent session (capped at 10k chars).",
        parameters={
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "The session run_id"},
            },
            "required": ["run_id"],
        },
        handler=sessions_history,
        permission="none",
    ))

    # --- sessions_kill ---
    async def sessions_kill(run_id: str) -> str:
        session = registry.get(run_id)
        if not session:
            return f"Error: no session '{run_id}'"
        if session.status != "running":
            return f"Session {run_id} is already {session.status}"
        # Cancel the asyncio task if running
        if session._task and not session._task.done():
            session._task.cancel()
        registry.mark_complete(run_id, "error", "Cancelled by parent")
        return f"Cancelled session {run_id} ({session.label})"

    tools.append(ToolDefinition(
        name="sessions_kill",
        description="Cancel a running subagent session.",
        parameters={
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "The session run_id to cancel"},
            },
            "required": ["run_id"],
        },
        handler=sessions_kill,
        permission="none",
    ))

    return tools
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/joseph/Projects/conduit && python -m pytest server/tests/test_subagent_tools.py -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add server/subagents.py server/tests/test_subagent_tools.py
git commit -m "feat: add subagent execution engine and session tools"
```

---

## Task 8: Wire Subagents into Startup & Agent Resolution

**Files:**
- Modify: `server/app.py:223-380` (init subagent registry at startup, init DB)
- Modify: `server/app.py:476-675` (drain announcements at top of handle_message)
- Modify: `server/agents.py:287-293` (replace old comms tools with session tools)
- Test: `server/tests/test_subagents_integration.py`

**Step 1: Write the failing test**

Create `server/tests/test_subagents_integration.py`:

```python
"""Integration tests for subagent wiring."""


def test_announcement_drain():
    """drain_announcements should return and clear pending announcements."""
    from server.subagents import queue_announcement, drain_announcements

    queue_announcement("ws:main:c1", {"run_id": "abc", "status": "done", "result_summary": "Found it"})
    queue_announcement("ws:main:c1", {"run_id": "def", "status": "error", "result_summary": "Failed"})

    announces = drain_announcements("ws:main:c1")
    assert len(announces) == 2
    # Should be drained now
    assert drain_announcements("ws:main:c1") == []


def test_session_registry_init():
    """init_registry should create a module-level registry."""
    from server.subagents import init_registry, get_registry
    registry = init_registry(max_spawn_depth=3, max_children=10)
    assert registry is not None
    assert get_registry() is registry
    assert registry.max_spawn_depth == 3
```

**Step 2: Run tests to verify they pass (functions already exist)**

Run: `cd /home/joseph/Projects/conduit && python -m pytest server/tests/test_subagents_integration.py -v`
Expected: PASS

**Step 3: Wire subagent init into `app.py` lifespan**

In `server/app.py`, after plugin loading (added in Task 5), add:

```python
    # Initialize subagent system
    if config.SUBAGENTS_ENABLED:
        try:
            from .subagents import init_registry, init_db as init_subagent_db
            init_registry(
                max_spawn_depth=config.SUBAGENTS_MAX_SPAWN_DEPTH,
                max_children=config.SUBAGENTS_MAX_CHILDREN,
                default_timeout=config.SUBAGENTS_DEFAULT_TIMEOUT,
            )
            await init_subagent_db()
            log.info("Subagent system initialized")
        except Exception as e:
            log.warning("Subagent system not available: %s", e)
```

**Step 4: Drain announcements at top of `handle_message`**

In `server/app.py`, in the `handle_message` function, after `await db.add_message(conversation_id, "user", content)` (line 486), add:

```python
    # Drain subagent announcements
    if config.SUBAGENTS_ENABLED:
        try:
            from .subagents import drain_announcements
            session_key = f"websocket:main:{conversation_id}"
            announces = drain_announcements(session_key)
            if announces:
                lines = []
                for a in announces:
                    lines.append(
                        f'[Subagent Complete] "{a.get("label", "?")}" '
                        f'({a.get("status", "?")}). '
                        f'Result: {a.get("result_summary", "")}'
                    )
                announcement_text = "\n".join(lines)
                content = announcement_text + "\n\n" + content
                # Update the stored message with announcement context
                log.info("Injected %d subagent announcements", len(announces))
        except Exception as e:
            log.warning("Announcement drain failed: %s", e)
```

**Step 5: Replace old comms tools with session tools in `agents.py`**

In `server/agents.py`, modify `get_comms_tools` (line 287-293) to also return session tools when subagents are enabled:

```python
    def get_comms_tools(self, agent_id: str) -> list[ToolDefinition]:
        """Return inter-agent communication tools if enabled for this agent."""
        tools = []

        # Legacy comms tools
        if self._comms_enabled:
            if not self._comms_allow or agent_id in self._comms_allow:
                tools.extend(_build_comms_tools(self, agent_id))

        # Session tools (subagents)
        try:
            from . import config
            if config.SUBAGENTS_ENABLED:
                from .subagents import get_registry, build_session_tools
                registry = get_registry()
                if registry:
                    session_key = f"agent:{agent_id}"
                    session_tools = build_session_tools(
                        registry, agent_id, session_key, depth=0,
                        agent_registry=self,
                    )
                    tools.extend(session_tools)
        except Exception:
            pass

        return tools
```

**Step 6: Run all tests**

Run: `cd /home/joseph/Projects/conduit && python -m pytest server/tests/ -v`
Expected: All tests PASS

**Step 7: Commit**

```bash
git add server/app.py server/agents.py server/tests/test_subagents_integration.py
git commit -m "feat: wire subagents into startup, message handling, and agent tool injection"
```

---

## Task 9: Skill Install Tool (ClawHub Fetch)

**Files:**
- Modify: `server/skills.py` (add `skill_install` tool function)
- Modify: `server/app.py` (register skill_install tool at startup)
- Test: `server/tests/test_skill_install.py`

**Step 1: Write the failing test**

Create `server/tests/test_skill_install.py`:

```python
"""Tests for the skill_install tool."""

import pytest


@pytest.mark.asyncio
async def test_skill_install_from_url(tmp_skills_dir, monkeypatch):
    """skill_install should download and save a SKILL.md from a URL."""
    from server import skills

    # Mock the HTTP fetch
    skill_content = """---
name: test-remote
description: A remote skill
---

# Test Remote Skill

Do the remote thing.
"""

    async def mock_fetch(url, timeout=15):
        return skill_content

    monkeypatch.setattr(skills, "_fetch_url", mock_fetch)
    monkeypatch.setattr(skills, "_skills_dir", str(tmp_skills_dir))

    result = await skills.skill_install(name="test-remote", source="https://example.com/SKILL.md")
    assert "Installed" in result or "installed" in result.lower()

    # Verify file was written
    skill_path = tmp_skills_dir / "test-remote" / "SKILL.md"
    assert skill_path.exists()
    assert "test-remote" in skill_path.read_text()


@pytest.mark.asyncio
async def test_skill_install_invalid_content(tmp_skills_dir, monkeypatch):
    """skill_install should reject content without valid frontmatter."""
    from server import skills

    async def mock_fetch(url, timeout=15):
        return "# Just markdown, no frontmatter"

    monkeypatch.setattr(skills, "_fetch_url", mock_fetch)
    monkeypatch.setattr(skills, "_skills_dir", str(tmp_skills_dir))

    result = await skills.skill_install(name="bad-skill", source="https://example.com/bad")
    assert "Error" in result or "error" in result.lower() or "invalid" in result.lower()
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/joseph/Projects/conduit && python -m pytest server/tests/test_skill_install.py -v`
Expected: FAIL (no `skill_install` function yet)

**Step 3: Add skill_install to `server/skills.py`**

Add to the end of `server/skills.py`:

```python
# Module-level skills dir reference (set during load_skills)
_skills_dir: str = ""


async def _fetch_url(url: str, timeout: int = 15) -> str:
    """Fetch content from a URL. Uses aiohttp if available, falls back to urllib."""
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                resp.raise_for_status()
                return await resp.text()
    except ImportError:
        import urllib.request
        import asyncio
        loop = asyncio.get_event_loop()
        req = urllib.request.Request(url)
        def _do():
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
        return await loop.run_in_executor(None, _do)


async def skill_install(name: str, source: str = "clawhub") -> str:
    """Install a skill from ClawHub or a URL.

    Args:
        name: Skill name (used as folder name)
        source: "clawhub" to fetch from ClawHub, or a URL to a SKILL.md
    """
    global _skills_dir

    if not _skills_dir:
        from . import config
        _skills_dir = os.path.expanduser(config.MARKDOWN_SKILLS_DIR)

    # Determine URL
    if source == "clawhub":
        url = f"https://clawhub.com/api/skills/{name}/download"
    elif source.startswith("http"):
        url = source
    else:
        return f"Error: invalid source '{source}'. Use 'clawhub' or a URL."

    # Fetch content
    try:
        content = await _fetch_url(url)
    except Exception as e:
        return f"Error fetching skill: {e}"

    # Validate
    skill = parse_skill_md(content, name)
    if skill is None:
        return f"Error: downloaded content is not a valid SKILL.md (no YAML frontmatter)"

    # Save to skills directory
    skill_dir = Path(_skills_dir) / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    # Reload into memory
    _skills.append(skill)

    return f"Installed skill '{skill['name']}' to {skill_dir}"
```

Also update `load_skills` to set `_skills_dir`:

In the existing `load_skills` function, add at the top:

```python
    global _skills, _skills_dir
    _skills_dir = os.path.expanduser(skills_dir)
```

(change from `global _skills` to `global _skills, _skills_dir`)

**Step 4: Register skill_install as a tool in `app.py`**

In `server/app.py`, after the markdown skills loading block (added in Task 3), add:

```python
        # Register skill_install tool
        try:
            from .skills import skill_install
            from .tools import register as register_tool
            from .tools.definitions import ToolDefinition
            register_tool(ToolDefinition(
                name="skill_install",
                description="Install a markdown skill from ClawHub or a URL.",
                parameters={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Skill name (folder name)"},
                        "source": {"type": "string", "description": "'clawhub' or a URL to SKILL.md"},
                    },
                    "required": ["name"],
                },
                handler=skill_install,
                permission="write",
            ))
        except Exception as e:
            log.warning("skill_install tool not available: %s", e)
```

**Step 5: Run tests**

Run: `cd /home/joseph/Projects/conduit && python -m pytest server/tests/test_skill_install.py -v`
Expected: PASS (2 tests)

**Step 6: Commit**

```bash
git add server/skills.py server/app.py server/tests/test_skill_install.py
git commit -m "feat: add skill_install tool for ClawHub and URL skill fetching"
```

---

## Task 10: End-to-End Smoke Test

**Files:**
- Test: `server/tests/test_smoke.py`

**Step 1: Write a smoke test that validates the full integration**

Create `server/tests/test_smoke.py`:

```python
"""Smoke tests — validate all new modules import and integrate correctly."""


def test_skills_module_imports():
    """server.skills should import without errors."""
    from server.skills import (
        discover_skills, parse_skill_md, build_skills_catalog,
        build_skills_context, load_skills, get_skills, get_skills_context,
        skill_install,
    )


def test_plugins_module_imports():
    """server.plugins should import without errors."""
    from server.plugins import (
        PluginAPI, discover_plugins, load_plugin, load_all_plugins,
        dispatch_hook, get_loaded_plugins,
    )


def test_subagents_module_imports():
    """server.subagents should import without errors."""
    from server.subagents import (
        SubagentSession, SessionRegistry, init_registry, get_registry,
        build_session_tools, queue_announcement, drain_announcements,
        init_db, persist_session, run_subagent,
    )


def test_config_has_all_new_sections():
    """All new config constants should be accessible."""
    from server import config
    # Markdown skills
    assert isinstance(config.MARKDOWN_SKILLS_ENABLED, bool)
    assert isinstance(config.MARKDOWN_SKILLS_DIR, str)
    assert isinstance(config.MARKDOWN_SKILLS_MAX_PER_TURN, int)
    # Plugins
    assert isinstance(config.PLUGINS_ENABLED, bool)
    assert isinstance(config.PLUGINS_DIR, str)
    # Subagents
    assert isinstance(config.SUBAGENTS_ENABLED, bool)
    assert isinstance(config.SUBAGENTS_MAX_SPAWN_DEPTH, int)
    assert isinstance(config.SUBAGENTS_MAX_CHILDREN, int)
    assert isinstance(config.SUBAGENTS_DEFAULT_TIMEOUT, int)
    assert isinstance(config.SUBAGENTS_SESSION_TTL_MINUTES, int)


def test_system_prompt_has_skills_placeholder():
    """The system prompt template should include {skills_context}."""
    from server import config
    assert "{skills_context}" in config.SYSTEM_PROMPT_TEMPLATE


def test_tool_registry_type():
    """Tool registry should be a dict of ToolDefinitions."""
    from server.tools import _TOOLS
    assert isinstance(_TOOLS, dict)
```

**Step 2: Run the full test suite**

Run: `cd /home/joseph/Projects/conduit && python -m pytest server/tests/ -v --tb=short`
Expected: All tests PASS

**Step 3: Commit**

```bash
git add server/tests/test_smoke.py
git commit -m "test: add smoke tests for skills, plugins, and subagents integration"
```

---

## Task 11: Create Default Skills Directory & Sample Skill

**Files:**
- Create: `~/.conduit/skills/` directory (on local machine)
- Create: `examples/skills/hello/SKILL.md` (sample skill in repo)

**Step 1: Create the skills directory structure**

```bash
mkdir -p ~/.conduit/skills
mkdir -p /home/joseph/Projects/conduit/examples/skills/hello
```

**Step 2: Write a sample skill**

Create `examples/skills/hello/SKILL.md`:

```markdown
---
name: hello
description: Greet the user warmly
metadata:
  conduit:
    agent: default
---

# Hello Skill

When the user says hello, greet them warmly by name (Joseph).
Be friendly and natural. If it's morning, say good morning. If evening, good evening.
```

**Step 3: Commit**

```bash
git add examples/skills/hello/SKILL.md
git commit -m "docs: add sample hello skill and skills directory structure"
```

---

## Summary

| Task | Description | New Files | Modified Files |
|------|-------------|-----------|----------------|
| 0 | Test infrastructure | `server/tests/__init__.py`, `conftest.py` | — |
| 1 | Config sections | `test_config_new_sections.py` | `config.py`, `config.yaml` |
| 2 | Skill parsing | `skills.py`, `test_skills.py` | — |
| 3 | Skills wiring | `test_skills_integration.py` | `config.yaml`, `app.py` |
| 4 | Plugin SDK | `plugins.py`, `test_plugins.py` | — |
| 5 | Plugins wiring | `test_plugins_integration.py` | `app.py`, `agent.py` |
| 6 | Subagent model | `subagents.py`, `test_subagents.py` | — |
| 7 | Subagent tools | `test_subagent_tools.py` | `subagents.py` |
| 8 | Subagents wiring | `test_subagents_integration.py` | `app.py`, `agents.py` |
| 9 | Skill install | `test_skill_install.py` | `skills.py`, `app.py` |
| 10 | Smoke tests | `test_smoke.py` | — |
| 11 | Sample skill | `examples/skills/hello/SKILL.md` | — |
