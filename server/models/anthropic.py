"""Anthropic Claude provider — supports any Claude model (Haiku, Opus, etc.)."""

import json
import logging
from collections.abc import AsyncIterator

import anthropic

from .base import BaseProvider, StreamChunk, StreamDone, StreamToolCall, ToolCall, Usage

log = logging.getLogger("conduit.anthropic")


class AnthropicProvider(BaseProvider):

    def __init__(self, name: str, api_key: str, model: str):
        self.name = name
        self.model = model
        self.client = anthropic.AsyncAnthropic(api_key=api_key)

    @property
    def supports_tools(self) -> bool:
        return True

    async def stream(self, messages: list[dict], system: str = "",
                     tools: list | None = None) -> AsyncIterator[StreamChunk | StreamDone | StreamToolCall]:
        kwargs = dict(
            model=self.model,
            max_tokens=4096,
            system=system or "You are a helpful assistant.",
            messages=messages,
        )
        if tools:
            kwargs["tools"] = tools

        input_tokens = 0
        output_tokens = 0

        # Accumulate tool use blocks from events
        current_tool_id = None
        current_tool_name = None
        current_tool_json = ""
        tool_calls: list[ToolCall] = []

        async with self.client.messages.stream(**kwargs) as stream:
            async for event in stream:
                # Text delta
                if event.type == "content_block_start":
                    block = event.content_block
                    if block.type == "tool_use":
                        current_tool_id = block.id
                        current_tool_name = block.name
                        current_tool_json = ""

                elif event.type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield StreamChunk(text=delta.text)
                    elif delta.type == "input_json_delta":
                        current_tool_json += delta.partial_json

                elif event.type == "content_block_stop":
                    if current_tool_id:
                        try:
                            args = json.loads(current_tool_json) if current_tool_json else {}
                        except json.JSONDecodeError:
                            args = {}
                        tool_calls.append(ToolCall(
                            id=current_tool_id,
                            name=current_tool_name,
                            arguments=args,
                        ))
                        current_tool_id = None
                        current_tool_name = None
                        current_tool_json = ""

            # Get final message for usage
            msg = await stream.get_final_message()
            input_tokens = msg.usage.input_tokens
            output_tokens = msg.usage.output_tokens

        if tool_calls:
            yield StreamToolCall(tool_calls=tool_calls)

        yield StreamDone(usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens))

    def format_tool_calls_message(self, text: str, tool_calls: list[ToolCall]) -> dict:
        """Anthropic-format assistant message with tool use blocks."""
        content = []
        if text:
            content.append({"type": "text", "text": text})
        for tc in tool_calls:
            content.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.name,
                "input": tc.arguments,
            })
        return {"role": "assistant", "content": content}

    def format_tool_result(self, tool_call_id: str, name: str, result: str) -> dict:
        """Anthropic-format tool result message."""
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": result,
                }
            ],
        }
