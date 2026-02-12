"""Multi-agent architecture — config-driven agent routing, isolation, and communication."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import config, db
from .tools.definitions import ToolDefinition

if TYPE_CHECKING:
    from .models.base import BaseProvider

log = logging.getLogger("conduit.agents")

# Context variable for tracking inter-agent call depth (prevents infinite recursion)
_agent_depth: contextvars.ContextVar[int] = contextvars.ContextVar("agent_depth", default=0)

# System commands that should never be treated as agent bindings
_SYSTEM_COMMANDS = frozenset({
    "/clear", "/help", "/models", "/model", "/usage", "/memories",
    "/permissions", "/remind", "/schedule", "/start", "/agents",
})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class AgentConfig:
    """Static configuration for a single agent."""
    id: str
    provider: str
    default: bool = False
    prompt_override: str = ""
    prompt_extend: str = ""
    tools_allow: list[str] = field(default_factory=list)
    tools_deny: list[str] = field(default_factory=list)
    max_turns: int = 0  # 0 = use global default
    isolated: bool = False


@dataclass
class BindingContext:
    """Runtime context used to resolve which agent handles a message."""
    channel: str = ""   # "websocket", "telegram", "api"
    peer: str = ""      # chat_id or connection identifier
    command: str = ""   # e.g. "/code", "/research"
    content: str = ""   # raw message text (for future pattern matching)


@dataclass
class Binding:
    """Maps a context pattern to an agent."""
    agent_id: str
    command: str = ""
    channel: str = ""
    peer: str = ""

    @property
    def specificity(self) -> int:
        """Higher = more specific. peer(100) > command(10) > channel(1)."""
        score = 0
        if self.peer:
            score += 100
        if self.command:
            score += 10
        if self.channel:
            score += 1
        return score

    def matches(self, ctx: BindingContext) -> bool:
        """All non-empty fields must match the context."""
        if self.command and self.command != ctx.command:
            return False
        if self.channel and self.channel != ctx.channel:
            return False
        if self.peer and self.peer != ctx.peer:
            return False
        return True


# ---------------------------------------------------------------------------
# Agent wrapper
# ---------------------------------------------------------------------------

class Agent:
    """Live agent instance wrapping config + provider reference."""

    def __init__(self, cfg: AgentConfig, provider: "BaseProvider"):
        self.cfg = cfg
        self.provider = provider

    @property
    def id(self) -> str:
        return self.cfg.id

    def get_tools(self, all_tools: list[ToolDefinition],
                  extra_tools: list[ToolDefinition] | None = None) -> list[ToolDefinition]:
        """Filter the global tool list per agent config (allow/deny lists).

        extra_tools (e.g. inter-agent comms) bypass allow/deny filters.
        """
        tools = list(all_tools)

        if self.cfg.tools_allow:
            tools = [t for t in tools if t.name in self.cfg.tools_allow]
        elif self.cfg.tools_deny:
            tools = [t for t in tools if t.name not in self.cfg.tools_deny]

        # Extra tools (comms) are always included, bypassing allow/deny
        if extra_tools:
            tools.extend(extra_tools)

        return tools

    def get_system_prompt(self, base_prompt: str) -> str:
        """Override or extend the base system prompt."""
        if self.cfg.prompt_override:
            return self.cfg.prompt_override
        if self.cfg.prompt_extend:
            return base_prompt + "\n\n" + self.cfg.prompt_extend
        return base_prompt

    def get_max_turns(self) -> int:
        """Agent-specific max_turns, or fall back to global config."""
        return self.cfg.max_turns or config.MAX_AGENT_TURNS

    def get_session_key(self, channel: str, peer: str) -> str:
        """Session key for Claude Code or other stateful providers."""
        return f"agent_session:{self.id}:{channel}:{peer}"


# ---------------------------------------------------------------------------
# SilentAdapter — for inter-agent calls (no user-facing output)
# ---------------------------------------------------------------------------

class SilentAdapter:
    """Mimics ConnectionManager interface, absorbs all output silently.

    Used for inter-agent communication where no user-facing output is needed.
    Auto-approves reads, denies writes (unless AUTO_APPROVE_ALL).
    """

    def __init__(self):
        self.chunks: list[str] = []

    async def send_chunk(self, ws, content: str):
        self.chunks.append(content)

    async def send_done(self, ws):
        pass

    async def send_meta(self, ws, model: str = "", input_tokens: int = 0, output_tokens: int = 0):
        pass

    async def send_typing(self, ws):
        pass

    async def send_error(self, ws, message: str):
        log.warning("SilentAdapter error: %s", message)

    async def send_tool_start(self, ws, tool_call_id: str, name: str, arguments: dict):
        pass

    async def send_tool_done(self, ws, tool_call_id: str, name: str,
                             result: str = "", error: str = ""):
        pass

    async def request_permission(self, ws, action: str, detail: dict) -> bool:
        if config.AUTO_APPROVE_ALL:
            return True
        if config.AUTO_APPROVE_READS and action.startswith("read"):
            return True
        return False

    def get_response(self) -> str:
        return "".join(self.chunks)


# ---------------------------------------------------------------------------
# AgentRegistry
# ---------------------------------------------------------------------------

class AgentRegistry:
    """Central registry of agents and bindings, built from config."""

    def __init__(self):
        self._agents: dict[str, Agent] = {}
        self._bindings: list[Binding] = []
        self._default_id: str = ""
        self._comms_enabled: bool = False
        self._comms_allow: list[str] = []
        self._comms_max_rounds: int = 5
        # KV store for spawned task results
        self._task_results: dict[str, dict] = {}

    @classmethod
    def build(cls, agents_cfg: list[dict], bindings_cfg: list[dict],
              comms_cfg: dict, providers: dict[str, "BaseProvider"]) -> "AgentRegistry":
        """Factory: build registry from config dicts + live provider instances."""
        registry = cls()

        for acfg in agents_cfg:
            agent_id = acfg.get("id", "")
            provider_name = acfg.get("provider", "")
            provider = providers.get(provider_name)
            if not provider:
                log.warning("Agent '%s' references unknown provider '%s' — skipping",
                            agent_id, provider_name)
                continue

            cfg = AgentConfig(
                id=agent_id,
                provider=provider_name,
                default=acfg.get("default", False),
                prompt_override=acfg.get("prompt_override", ""),
                prompt_extend=acfg.get("prompt_extend", ""),
                tools_allow=acfg.get("tools_allow", []),
                tools_deny=acfg.get("tools_deny", []),
                max_turns=acfg.get("max_turns", 0),
                isolated=acfg.get("isolated", False),
            )
            agent = Agent(cfg, provider)
            registry._agents[agent_id] = agent
            if cfg.default:
                registry._default_id = agent_id

        # If no explicit default, use the first agent
        if not registry._default_id and registry._agents:
            registry._default_id = next(iter(registry._agents))

        # Build bindings sorted by specificity (most specific first)
        for bcfg in bindings_cfg:
            binding = Binding(
                agent_id=bcfg.get("agent_id", ""),
                command=bcfg.get("command", ""),
                channel=bcfg.get("channel", ""),
                peer=bcfg.get("peer", ""),
            )
            if binding.agent_id not in registry._agents:
                log.warning("Binding references unknown agent '%s' — skipping",
                            binding.agent_id)
                continue
            if binding.command and not binding.command.startswith("/"):
                log.warning("Binding command '%s' doesn't start with '/' — will never match",
                            binding.command)
            registry._bindings.append(binding)

        registry._bindings.sort(key=lambda b: b.specificity, reverse=True)

        # Communication config
        registry._comms_enabled = comms_cfg.get("enabled", False)
        registry._comms_allow = comms_cfg.get("allow", [])
        registry._comms_max_rounds = comms_cfg.get("max_rounds", 5)

        log.info("Agent registry built: %d agents, %d bindings, comms=%s",
                 len(registry._agents), len(registry._bindings),
                 registry._comms_enabled)

        return registry

    def resolve(self, ctx: BindingContext) -> Agent | None:
        """Walk bindings by specificity, return matching agent or default."""
        for binding in self._bindings:
            if binding.matches(ctx):
                agent = self._agents.get(binding.agent_id)
                if agent:
                    log.debug("Resolved agent '%s' for context: cmd=%s ch=%s peer=%s",
                              agent.id, ctx.command, ctx.channel, ctx.peer)
                    return agent

        # Fall back to default
        if self._default_id:
            return self._agents.get(self._default_id)
        return None

    def get(self, agent_id: str) -> Agent | None:
        """Direct lookup by agent ID."""
        return self._agents.get(agent_id)

    def get_comms_tools(self, agent_id: str) -> list[ToolDefinition]:
        """Return inter-agent communication tools if enabled for this agent."""
        if not self._comms_enabled:
            return []
        if self._comms_allow and agent_id not in self._comms_allow:
            return []
        return _build_comms_tools(self, agent_id)

    def list_agents(self) -> list[dict]:
        """Summary for the /agents command."""
        result = []
        for agent in self._agents.values():
            bindings = [b.command for b in self._bindings
                        if b.agent_id == agent.id and b.command]
            result.append({
                "id": agent.id,
                "provider": agent.cfg.provider,
                "model": agent.provider.model,
                "default": agent.cfg.default,
                "commands": bindings,
                "tools_allow": agent.cfg.tools_allow if agent.cfg.tools_allow else [],
                "max_turns": agent.get_max_turns(),
            })
        return result

    @property
    def has_agents(self) -> bool:
        return bool(self._agents)


# ---------------------------------------------------------------------------
# Inter-agent communication tools
# ---------------------------------------------------------------------------

def _build_comms_tools(registry: AgentRegistry, caller_id: str) -> list[ToolDefinition]:
    """Build the agent_send/agent_spawn/agent_get_result tools bound to this registry."""

    available_ids = [a.id for a in registry._agents.values() if a.id != caller_id]
    agents_desc = ", ".join(available_ids) if available_ids else "(none)"

    async def agent_send(agent_id: str, message: str) -> str:
        """Send a message to another agent and get a synchronous response."""
        depth = _agent_depth.get()
        if depth >= registry._comms_max_rounds:
            return f"Error: max inter-agent depth ({registry._comms_max_rounds}) reached."

        target = registry.get(agent_id)
        if not target:
            return f"Error: unknown agent '{agent_id}'. Available: {agents_desc}"

        # Prevent self-calls
        if agent_id == caller_id:
            return "Error: cannot send message to self."

        log.info("Inter-agent: %s -> %s: %s", caller_id, agent_id, message[:100])

        # Increment depth for the duration of this call
        token = _agent_depth.set(depth + 1)
        try:
            # Run the target agent's full pipeline
            from . import agent as agent_mod
            from .tools import get_all as get_all_tools
            from .app import render_system_prompt_async

            all_tools = get_all_tools()
            comms = registry.get_comms_tools(agent_id)
            tools = target.get_tools(all_tools, extra_tools=comms)
            system = await render_system_prompt_async(query=message)
            system = target.get_system_prompt(system)

            adapter = SilentAdapter()
            messages = [{"role": "user", "content": message}]

            if getattr(target.provider, 'manages_own_tools', False):
                response_text, usage, _, _ = await target.provider.run(
                    prompt=message, session_id=None, ws=None, manager=adapter,
                )
            elif target.provider.supports_tools and config.TOOLS_ENABLED and tools:
                response_text, usage = await agent_mod.run_agent_loop(
                    messages, system, target.provider, tools, None, adapter,
                    max_turns=target.get_max_turns(),
                )
            else:
                response_text, usage = await target.provider.generate(
                    messages, system=system,
                )

            result = adapter.get_response() or response_text
            if usage:
                await db.log_usage(target.provider.name, target.provider.model,
                                   usage.input_tokens, usage.output_tokens)
            return result or "(no response)"
        finally:
            _agent_depth.reset(token)

    async def agent_spawn(agent_id: str, task: str) -> str:
        """Spawn a background task on another agent. Returns a task_id."""
        target = registry.get(agent_id)
        if not target:
            return f"Error: unknown agent '{agent_id}'. Available: {agents_desc}"

        task_id = str(uuid.uuid4())[:8]
        registry._task_results[task_id] = {"status": "running", "result": None}

        async def _run():
            try:
                result = await agent_send(agent_id, task)
                registry._task_results[task_id] = {"status": "done", "result": result}
            except Exception as e:
                registry._task_results[task_id] = {"status": "error", "result": str(e)}

        registry._task_results[task_id]["_task"] = asyncio.create_task(_run())
        log.info("Spawned task %s: %s -> %s", task_id, caller_id, agent_id)
        return f"Task spawned: {task_id} (agent: {agent_id})"

    async def agent_get_result(task_id: str) -> str:
        """Check the status/result of a previously spawned agent task."""
        entry = registry._task_results.get(task_id)
        if not entry:
            return f"Error: unknown task_id '{task_id}'"
        if entry["status"] == "running":
            return "Status: still running"
        return f"Status: {entry['status']}\n\nResult:\n{entry['result']}"

    # Build ToolDefinition objects
    send_tool = ToolDefinition(
        name="agent_send",
        description=(
            f"Send a message to another agent and wait for their response. "
            f"Available agents: {agents_desc}. Use this to delegate tasks to "
            f"specialized agents (e.g. research, coding, analysis)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": f"Target agent ID. One of: {agents_desc}",
                },
                "message": {
                    "type": "string",
                    "description": "The message/task to send to the agent.",
                },
            },
            "required": ["agent_id", "message"],
        },
        handler=agent_send,
        permission="none",
    )

    spawn_tool = ToolDefinition(
        name="agent_spawn",
        description=(
            f"Spawn a background task on another agent. Returns immediately with a "
            f"task_id you can check later with agent_get_result. "
            f"Available agents: {agents_desc}."
        ),
        parameters={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": f"Target agent ID. One of: {agents_desc}",
                },
                "task": {
                    "type": "string",
                    "description": "The task description to send to the agent.",
                },
            },
            "required": ["agent_id", "task"],
        },
        handler=agent_spawn,
        permission="none",
    )

    result_tool = ToolDefinition(
        name="agent_get_result",
        description="Check the status and result of a previously spawned agent task.",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The task_id returned by agent_spawn.",
                },
            },
            "required": ["task_id"],
        },
        handler=agent_get_result,
        permission="none",
    )

    return [send_tool, spawn_tool, result_tool]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_command(text: str) -> str:
    """Extract /command prefix from message text.

    Returns the command (e.g. "/code") or empty string if not a command
    or if it's a system command that shouldn't route to an agent.
    """
    if not text or text[0] != "/":
        return ""

    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()

    # Don't intercept system commands
    if cmd in _SYSTEM_COMMANDS:
        return ""

    return cmd
