"""Python plugin system for Conduit — PluginAPI, discovery, loading, and hook event bus."""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable

from server.tools.definitions import ToolDefinition

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global hook event bus: event_name -> list of async handler callables
# ---------------------------------------------------------------------------
_hooks: dict[str, list[Callable[..., Awaitable[dict | None]]]] = {}

# Registry of loaded plugin metadata (for introspection / status endpoints)
_loaded_plugins: list[dict] = []


# ---------------------------------------------------------------------------
# PluginAPI — the interface handed to each plugin's register() function
# ---------------------------------------------------------------------------
@dataclass
class PluginAPI:
    """API surface exposed to plugins during registration."""

    id: str
    config: dict[str, Any]
    _tools: list[ToolDefinition] = field(default_factory=list)
    _hooks: list[tuple[str, Callable]] = field(default_factory=list)
    _registered_skills: list[dict] = field(default_factory=list)

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[..., Awaitable[str]],
        permission: str = "none",
    ) -> None:
        """Register a tool that becomes available to the agent."""
        tool = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            permission=permission,
        )
        self._tools.append(tool)
        logger.debug("Plugin %s registered tool: %s", self.id, name)

    def register_hook(self, event: str, handler: Callable) -> None:
        """Register a hook handler for an event (e.g. before_agent_start)."""
        self._hooks.append((event, handler))
        logger.debug("Plugin %s registered hook: %s", self.id, event)

    def register_skill(self, name: str, description: str, body: str) -> None:
        """Register a skill (markdown instruction block) dynamically."""
        self._registered_skills.append({
            "name": name,
            "description": description,
            "body": body,
            "source_plugin": self.id,
        })
        logger.debug("Plugin %s registered skill: %s", self.id, name)

    def log(self, msg: str, level: str = "info") -> None:
        """Convenience logger for plugins."""
        getattr(logger, level, logger.info)(f"[plugin:{self.id}] {msg}")


# ---------------------------------------------------------------------------
# Discovery — scan a directory for plugin.json manifests
# ---------------------------------------------------------------------------
def discover_plugins(plugins_dir: str) -> list[dict]:
    """Scan *plugins_dir* for subdirectories containing a valid plugin.json.

    Returns a list of manifest dicts, each augmented with a ``_path`` key
    pointing to the plugin directory.
    """
    base = Path(plugins_dir)
    if not base.is_dir():
        logger.debug("Plugins directory does not exist: %s", plugins_dir)
        return []

    found: list[dict] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        manifest_path = child / "plugin.json"
        if not manifest_path.exists():
            logger.debug("Skipping %s — no plugin.json", child.name)
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
            manifest["_path"] = str(child)
            found.append(manifest)
            logger.info("Discovered plugin: %s (%s)", manifest.get("id", child.name), child)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Invalid plugin.json in %s: %s", child.name, exc)
    return found


# ---------------------------------------------------------------------------
# Loading — import a single plugin and call its register() function
# ---------------------------------------------------------------------------
def load_plugin(
    plugin_path: Path,
    config: dict[str, Any] | None = None,
) -> tuple[list[ToolDefinition], list[tuple[str, Callable]], list[dict]] | None:
    """Import the plugin at *plugin_path* and invoke its ``register(api)`` entry point.

    Returns ``(tools, hooks, skills)`` on success, or ``None`` on failure.
    """
    manifest_path = plugin_path / "plugin.json"
    init_path = plugin_path / "__init__.py"

    if not manifest_path.exists() or not init_path.exists():
        logger.warning("Plugin at %s missing plugin.json or __init__.py", plugin_path)
        return None

    try:
        manifest = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Cannot read plugin.json at %s: %s", plugin_path, exc)
        return None

    plugin_id = manifest.get("id", plugin_path.name)
    module_name = f"_conduit_plugin_{plugin_id.replace('-', '_')}"

    # Temporarily add plugin dir's parent to sys.path so relative imports work
    parent_dir = str(plugin_path.parent)
    path_added = parent_dir not in sys.path
    if path_added:
        sys.path.insert(0, parent_dir)

    try:
        spec = importlib.util.spec_from_file_location(module_name, str(init_path))
        if spec is None or spec.loader is None:
            logger.warning("Could not create module spec for plugin %s", plugin_id)
            return None

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        if not hasattr(module, "register"):
            logger.warning("Plugin %s has no register() function", plugin_id)
            return None

        api = PluginAPI(id=plugin_id, config=config or {})
        module.register(api)

        logger.info(
            "Loaded plugin %s — %d tools, %d hooks, %d skills",
            plugin_id,
            len(api._tools),
            len(api._hooks),
            len(api._registered_skills),
        )
        return (api._tools, api._hooks, api._registered_skills)

    except Exception as exc:
        logger.warning("Failed to load plugin %s: %s", plugin_id, exc, exc_info=True)
        return None
    finally:
        # Clean up sys.path
        if path_added and parent_dir in sys.path:
            sys.path.remove(parent_dir)
        # Clean up module from sys.modules to allow re-loading
        sys.modules.pop(module_name, None)


# ---------------------------------------------------------------------------
# Bulk loading — discover + load all plugins, wire hooks into global bus
# ---------------------------------------------------------------------------
def load_all_plugins(
    plugins_dir: str,
    plugin_configs: dict[str, dict] | None = None,
) -> tuple[list[ToolDefinition], list[dict]]:
    """Discover and load all plugins from *plugins_dir*.

    Returns ``(all_tools, all_skills)``.
    Hooks are registered into the global ``_hooks`` bus as a side effect.
    """
    global _loaded_plugins
    manifests = discover_plugins(plugins_dir)
    all_tools: list[ToolDefinition] = []
    all_skills: list[dict] = []
    configs = plugin_configs or {}

    for manifest in manifests:
        plugin_id = manifest.get("id", "unknown")
        plugin_path = Path(manifest["_path"])
        config = configs.get(plugin_id, {})

        result = load_plugin(plugin_path, config=config)
        if result is None:
            continue

        tools, hooks, skills = result
        all_tools.extend(tools)
        all_skills.extend(skills)

        # Wire hooks into the global event bus
        for event, handler in hooks:
            _hooks.setdefault(event, []).append(handler)

        _loaded_plugins.append({
            "id": plugin_id,
            "name": manifest.get("name", plugin_id),
            "version": manifest.get("version", "0.0.0"),
            "description": manifest.get("description", ""),
            "tools": [t.name for t in tools],
            "hooks": [e for e, _ in hooks],
            "skills": [s["name"] for s in skills],
        })

    logger.info(
        "Plugin loading complete: %d plugins, %d tools, %d skills, %d hook events",
        len(_loaded_plugins),
        len(all_tools),
        len(all_skills),
        len(_hooks),
    )
    return all_tools, all_skills


# ---------------------------------------------------------------------------
# Hook dispatch — run all handlers for a given event
# ---------------------------------------------------------------------------
async def dispatch_hook(event: str, **kwargs: Any) -> dict | None:
    """Run all registered handlers for *event*, passing **kwargs to each.

    Handlers may return a dict of overrides.  Dicts are merged left-to-right
    so the last handler wins on key conflicts.  Returns the merged dict, or
    ``None`` if no handler returned anything.
    """
    handlers = _hooks.get(event)
    if not handlers:
        return None

    merged: dict[str, Any] = {}
    for handler in handlers:
        try:
            result = await handler(**kwargs)
            if isinstance(result, dict):
                merged.update(result)
        except Exception as exc:
            logger.warning("Hook handler error for %s: %s", event, exc, exc_info=True)

    return merged if merged else None


# ---------------------------------------------------------------------------
# Introspection
# ---------------------------------------------------------------------------
def get_loaded_plugins() -> list[dict]:
    """Return metadata for all successfully loaded plugins."""
    return list(_loaded_plugins)
