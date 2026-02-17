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
