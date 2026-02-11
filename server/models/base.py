"""Base provider ABC — all model providers implement this."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class StreamChunk:
    """A single piece of streamed text."""
    text: str


@dataclass
class StreamDone:
    """Signals end of stream, carries usage info."""
    usage: Usage


@dataclass
class ToolCall:
    """A single tool call request from the model."""
    id: str
    name: str
    arguments: dict


@dataclass
class StreamToolCall:
    """Yielded when the model wants to call tools instead of (or after) text."""
    tool_calls: list[ToolCall] = field(default_factory=list)


class BaseProvider(ABC):
    """Abstract base for all model providers."""

    name: str  # e.g. "ollama", "gemini", "opus"
    model: str  # e.g. "llama3.1", "gemini-2.0-flash"

    @property
    def supports_tools(self) -> bool:
        """Whether this provider supports tool/function calling."""
        return False

    @abstractmethod
    async def stream(self, messages: list[dict], system: str = "",
                     tools: list | None = None) -> AsyncIterator[StreamChunk | StreamDone | StreamToolCall]:
        """Stream a response. Yields StreamChunk objects, then a final StreamDone.

        Args:
            messages: List of {"role": "user"|"assistant", "content": "..."} dicts.
            system: System prompt.
            tools: Optional list of tool definitions in provider-native format.
        """
        ...

    def format_tool_calls_message(self, text: str, tool_calls: list[ToolCall]) -> dict:
        """Build a provider-specific assistant message containing tool calls."""
        raise NotImplementedError(f"{self.name} does not support tools")

    def format_tool_result(self, tool_call_id: str, name: str, result: str) -> dict:
        """Build a provider-specific tool result message."""
        raise NotImplementedError(f"{self.name} does not support tools")

    async def generate(self, messages: list[dict], system: str = "") -> tuple[str, Usage]:
        """Non-streaming convenience method — collects full response."""
        parts = []
        usage = Usage()
        async for item in self.stream(messages, system):
            if isinstance(item, StreamChunk):
                parts.append(item.text)
            elif isinstance(item, StreamDone):
                usage = item.usage
        return "".join(parts), usage
