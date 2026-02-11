"""OpenAI-compatible provider — works with Ollama, NIM, and any OpenAI-API server."""

import json
import logging
from collections.abc import AsyncIterator

import openai

from .base import BaseProvider, StreamChunk, StreamDone, StreamToolCall, ToolCall, Usage

log = logging.getLogger("conduit.openai_compat")


class OpenAICompatProvider(BaseProvider):

    def __init__(self, name: str, base_url: str, api_key: str, model: str):
        self.name = name
        self.model = model
        self.client = openai.AsyncOpenAI(base_url=base_url, api_key=api_key)

    @property
    def supports_tools(self) -> bool:
        return True

    async def stream(self, messages: list[dict], system: str = "",
                     tools: list | None = None) -> AsyncIterator[StreamChunk | StreamDone | StreamToolCall]:
        api_messages = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend(messages)

        kwargs = dict(
            model=self.model,
            messages=api_messages,
            stream=True,
            stream_options={"include_usage": True},
        )
        if tools:
            kwargs["tools"] = tools

        response = await self.client.chat.completions.create(**kwargs)

        usage = Usage()
        # Accumulate tool call deltas: {index: {"id": ..., "name": ..., "arguments": ...}}
        tc_accum: dict[int, dict] = {}

        async for chunk in response:
            # Usage info (usually on the final chunk)
            if chunk.usage:
                usage = Usage(
                    input_tokens=chunk.usage.prompt_tokens or 0,
                    output_tokens=chunk.usage.completion_tokens or 0,
                )

            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

            # Stream text content
            if delta.content:
                yield StreamChunk(text=delta.content)

            # Accumulate tool calls
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tc_accum:
                        tc_accum[idx] = {"id": "", "name": "", "arguments": ""}
                    if tc_delta.id:
                        tc_accum[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tc_accum[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tc_accum[idx]["arguments"] += tc_delta.function.arguments

        # If we accumulated tool calls, yield them before done
        if tc_accum:
            calls = []
            for idx in sorted(tc_accum):
                tc = tc_accum[idx]
                try:
                    args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                except json.JSONDecodeError:
                    log.warning("Failed to parse tool call arguments: %s", tc["arguments"])
                    args = {}
                calls.append(ToolCall(id=tc["id"], name=tc["name"], arguments=args))
            yield StreamToolCall(tool_calls=calls)

        yield StreamDone(usage=usage)

    def format_tool_calls_message(self, text: str, tool_calls: list[ToolCall]) -> dict:
        """OpenAI-format assistant message with tool calls."""
        msg = {"role": "assistant"}
        if text:
            msg["content"] = text
        else:
            msg["content"] = None
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments),
                },
            }
            for tc in tool_calls
        ]
        return msg

    def format_tool_result(self, tool_call_id: str, name: str, result: str) -> dict:
        """OpenAI-format tool result message."""
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result,
        }
