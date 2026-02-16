"""ChatGPT provider — Codex Responses API via OAuth (ChatGPT Plus subscription).

Uses the ChatGPT backend Codex endpoint with the OAuth access_token directly.
Endpoint: https://chatgpt.com/backend-api/codex/responses
Models: gpt-5.1-codex-mini, gpt-5.1-codex-max
"""

import json
import logging
from collections.abc import AsyncIterator

import httpx

from .base import BaseProvider, StreamChunk, StreamDone, StreamToolCall, ToolCall, Usage

log = logging.getLogger("conduit.chatgpt")

RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"


class ChatGPTProvider(BaseProvider):
    """ChatGPT Codex Responses API provider authenticated via ChatGPT Plus OAuth."""

    def __init__(self, name: str, model: str):
        self.name = name
        self.model = model

    @property
    def supports_tools(self) -> bool:
        return True

    async def stream(self, messages: list[dict], system: str = "",
                     tools: list | None = None) -> AsyncIterator[StreamChunk | StreamDone | StreamToolCall]:
        from ..chatgpt_auth import get_access_token_async

        token = await get_access_token_async()
        if not token:
            raise RuntimeError("ChatGPT: no access token available (auth required)")

        # Build the Responses API input from chat messages
        api_input = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            # Handle tool result messages
            if role == "tool":
                api_input.append({
                    "type": "function_call_output",
                    "call_id": msg.get("tool_call_id", ""),
                    "output": content if isinstance(content, str) else json.dumps(content),
                })
                continue

            # Handle assistant messages with tool calls
            if role == "assistant" and "tool_calls" in msg:
                # First add any text content
                if content:
                    api_input.append({"role": "assistant", "content": content})
                # Add function calls
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    api_input.append({
                        "type": "function_call",
                        "id": tc.get("id", ""),
                        "call_id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "arguments": func.get("arguments", "{}"),
                    })
                continue

            # Skip system messages — they go in the instructions field
            if role == "system":
                continue

            # Regular user/assistant messages
            api_input.append({"role": role, "content": content or ""})

        body = {
            "model": self.model,
            "input": api_input,
            "instructions": system or "You are a helpful assistant.",
            "stream": True,
            "store": False,
        }

        if tools:
            # Convert OpenAI function-calling tools format to Responses API format
            api_tools = []
            for t in tools:
                if t.get("type") == "function":
                    func = t["function"]
                    api_tools.append({
                        "type": "function",
                        "name": func["name"],
                        "description": func.get("description", ""),
                        "parameters": func.get("parameters", {}),
                    })
                else:
                    api_tools.append(t)
            body["tools"] = api_tools

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        usage = Usage()
        tool_calls: list[ToolCall] = []
        text_parts: list[str] = []

        async with httpx.AsyncClient(timeout=httpx.Timeout(
            connect=15, read=90, write=15, pool=15
        )) as client:
            async with client.stream("POST", RESPONSES_URL, json=body, headers=headers) as resp:
                if resp.status_code != 200:
                    error_body = await resp.aread()
                    try:
                        err = json.loads(error_body).get("error", {}).get("message", error_body.decode())
                    except Exception:
                        err = error_body.decode()[:200]
                    raise RuntimeError(f"ChatGPT API error ({resp.status_code}): {err}")

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload == "[DONE]":
                        break

                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    etype = event.get("type", "")

                    # Text output delta
                    if etype == "response.output_text.delta":
                        delta = event.get("delta", "")
                        if delta:
                            yield StreamChunk(text=delta)
                            text_parts.append(delta)

                    # Function call arguments delta
                    elif etype == "response.function_call_arguments.delta":
                        pass  # Accumulated on .done

                    # Function call completed
                    elif etype == "response.output_item.done":
                        item = event.get("item", {})
                        if item.get("type") == "function_call":
                            try:
                                args = json.loads(item.get("arguments", "{}"))
                            except json.JSONDecodeError:
                                args = {}
                            # Use `id` (fc_ prefix) not `call_id` (call_ prefix) —
                            # the Responses API requires IDs starting with 'fc'
                            tool_calls.append(ToolCall(
                                id=item.get("id", item.get("call_id", "")),
                                name=item.get("name", ""),
                                arguments=args,
                            ))

                    # Terminal events — extract usage and stop reading
                    elif etype in ("response.completed", "response.done"):
                        resp_obj = event.get("response", {})
                        usage_data = resp_obj.get("usage", {})
                        usage = Usage(
                            input_tokens=usage_data.get("input_tokens", 0),
                            output_tokens=usage_data.get("output_tokens", 0),
                        )
                        break

                    elif etype in ("response.failed", "response.incomplete"):
                        log.warning("ChatGPT stream ended with %s: %s", etype, event)
                        break

        if tool_calls:
            yield StreamToolCall(tool_calls=tool_calls)

        yield StreamDone(usage=usage)

    def format_tool_calls_message(self, text: str, tool_calls: list[ToolCall]) -> dict:
        """OpenAI-format assistant message with tool calls (for history)."""
        msg = {"role": "assistant", "content": text or None}
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
        """OpenAI-format tool result message (for history)."""
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result,
        }
