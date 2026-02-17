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
