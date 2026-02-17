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
