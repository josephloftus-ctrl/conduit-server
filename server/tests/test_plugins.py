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
    """Plugins with runtime errors should return None (graceful failure)."""
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
