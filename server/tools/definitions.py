"""Tool definition dataclass with multi-provider format converters."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable


@dataclass
class ToolDefinition:
    """A tool that the model can call."""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    handler: Callable[..., Awaitable[str]]
    permission: str = "none"  # "none" | "write" | "execute"

    def to_openai(self) -> dict:
        """OpenAI / NIM / Ollama function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic(self) -> dict:
        """Anthropic Claude tool format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }

    def to_gemini(self) -> dict:
        """Gemini function declaration format."""
        # Gemini uses the same JSON Schema style but nested in functionDeclarations
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }
