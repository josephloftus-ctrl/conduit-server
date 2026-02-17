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
