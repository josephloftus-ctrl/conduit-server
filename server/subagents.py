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
# Module-level registry & announcement infrastructure
# ---------------------------------------------------------------------------

_module_registry: SessionRegistry | None = None
_announcements: dict[str, list[dict]] = {}


def init_registry(max_spawn_depth=2, max_children=5, default_timeout=300):
    """Initialize the module-level SessionRegistry singleton."""
    global _module_registry
    _module_registry = SessionRegistry(max_spawn_depth=max_spawn_depth,
                                        max_children=max_children,
                                        default_timeout=default_timeout)
    return _module_registry


def get_registry():
    """Return the module-level SessionRegistry (or None if not initialized)."""
    return _module_registry


def queue_announcement(session_key: str, announcement: dict) -> None:
    """Store an announcement dict for a parent session key."""
    _announcements.setdefault(session_key, []).append(announcement)


def drain_announcements(session_key: str) -> list[dict]:
    """Pop and return all announcements for a session key."""
    return _announcements.pop(session_key, [])


async def init_db() -> None:
    """Initialize SQLite table for session persistence. No-op if aiosqlite unavailable."""
    try:
        import aiosqlite  # noqa: F401
        # Will be implemented when persistence is needed
        log.info("Subagent session DB ready")
    except ImportError:
        log.debug("aiosqlite not available, session persistence disabled")


async def persist_session(session: SubagentSession) -> None:
    """Persist a completed session to SQLite. No-op if DB not initialized."""
    pass  # Stub — will be implemented when persistence is needed


# ---------------------------------------------------------------------------
# Subagent execution engine
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

    all_tools = get_all_tools()
    tools = target.get_tools(all_tools)

    if session.depth < config.SUBAGENTS_MAX_SPAWN_DEPTH:
        child_tools = build_session_tools(
            _module_registry, session.child_agent_id,
            f"subagent:{session.run_id}", depth=session.depth,
            agent_registry=agent_registry,
        )
        tools.extend(child_tools)

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
    await persist_session(session)

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


# ---------------------------------------------------------------------------
# Session tools builder
# ---------------------------------------------------------------------------

def build_session_tools(
    registry: SessionRegistry,
    caller_agent_id: str,
    parent_session_key: str,
    depth: int,
    agent_registry: "AgentRegistry | None" = None,
) -> list:
    """Build the session management tools for an agent.

    Returns up to 5 ToolDefinition objects: sessions_spawn, sessions_send,
    sessions_list, sessions_history, sessions_kill.  sessions_spawn is
    excluded when ``depth >= registry.max_spawn_depth``.
    """
    from .tools.definitions import ToolDefinition

    tools: list[ToolDefinition] = []

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
                    "agent_id": {"type": "string", "description": "Target agent ID"},
                    "task": {"type": "string", "description": "The task for the subagent"},
                    "label": {"type": "string", "description": "Human-readable label (optional)"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (0 = default)"},
                    "cleanup": {"type": "string", "enum": ["keep", "delete"], "description": "Keep or delete session after completion"},
                },
                "required": ["agent_id", "task"],
            },
            handler=sessions_spawn,
            permission="none",
        ))

    # --- sessions_send ---
    async def sessions_send(run_id_or_label: str, message: str, timeout: int = 60) -> str:
        session = registry.get(run_id_or_label) or registry.get_by_label(run_id_or_label)
        if not session:
            return f"Error: no session found for '{run_id_or_label}'"
        if session.status != "running":
            return f"Error: session '{run_id_or_label}' is not running (status: {session.status})"
        return (
            f"Session {session.run_id} ({session.label}) is {session.status}. "
            f"Direct messaging to running sessions is not yet supported."
        )

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
                "status_filter": {"type": "string", "description": "Filter by status (optional)"},
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
        if len(result) > 10000:
            result = result[:10000] + "\n... (truncated)"
        return result

    tools.append(ToolDefinition(
        name="sessions_history",
        description="Get the conversation history of a subagent session.",
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
