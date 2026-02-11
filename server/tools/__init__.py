"""Tool registry — register and retrieve tool definitions."""

from __future__ import annotations

import logging
from .definitions import ToolDefinition

log = logging.getLogger("conduit.tools")

# Global registry
_TOOLS: dict[str, ToolDefinition] = {}


def register(tool: ToolDefinition):
    """Register a tool definition."""
    _TOOLS[tool.name] = tool
    log.debug("Registered tool: %s", tool.name)


def get_all() -> list[ToolDefinition]:
    """Return all registered tools."""
    return list(_TOOLS.values())


def get_tool(name: str) -> ToolDefinition | None:
    """Get a tool by name."""
    return _TOOLS.get(name)


def get_all_openai() -> list[dict]:
    """All tools in OpenAI format."""
    return [t.to_openai() for t in _TOOLS.values()]


def get_all_anthropic() -> list[dict]:
    """All tools in Anthropic format."""
    return [t.to_anthropic() for t in _TOOLS.values()]
