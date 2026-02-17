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
