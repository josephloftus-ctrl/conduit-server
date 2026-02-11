"""Google Gemini provider — supports both Vertex AI (GCP credits) and standalone API."""

import json
import logging
import uuid
from collections.abc import AsyncIterator

from google import genai
from google.genai import types

from .base import BaseProvider, StreamChunk, StreamDone, StreamToolCall, ToolCall, Usage

log = logging.getLogger("conduit.gemini")


class GeminiProvider(BaseProvider):

    def __init__(self, name: str, model: str,
                 api_key: str = "",
                 vertex: bool = False,
                 project: str = "",
                 location: str = "us-east4"):
        self.name = name
        self.model = model

        if vertex:
            self.client = genai.Client(
                vertexai=True,
                project=project,
                location=location,
            )
        else:
            self.client = genai.Client(api_key=api_key)

    @property
    def supports_tools(self) -> bool:
        return True

    async def stream(self, messages: list[dict], system: str = "",
                     tools: list | None = None) -> AsyncIterator[StreamChunk | StreamDone | StreamToolCall]:
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"

            # Handle Anthropic-style content blocks (list of dicts)
            if isinstance(msg.get("content"), list):
                parts = []
                for block in msg["content"]:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            parts.append(types.Part(text=block["text"]))
                        elif block.get("type") == "tool_use":
                            parts.append(types.Part(
                                function_call=types.FunctionCall(
                                    name=block["name"],
                                    args=block.get("input", {}),
                                )
                            ))
                        elif block.get("type") == "tool_result":
                            parts.append(types.Part(
                                function_response=types.FunctionResponse(
                                    name=block.get("name", "tool"),
                                    response={"result": block.get("content", "")},
                                )
                            ))
                    else:
                        parts.append(types.Part(text=str(block)))
                if parts:
                    contents.append(types.Content(role=role, parts=parts))
            elif msg.get("role") == "tool":
                # OpenAI-format tool result — convert to Gemini function_response
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part(
                        function_response=types.FunctionResponse(
                            name=msg.get("name", "tool"),
                            response={"result": msg.get("content", "")},
                        )
                    )],
                ))
            else:
                content_str = msg.get("content") or ""
                if content_str:
                    contents.append(types.Content(
                        role=role,
                        parts=[types.Part(text=content_str)],
                    ))

        cfg = types.GenerateContentConfig(
            max_output_tokens=4096,
        )
        if system:
            cfg.system_instruction = system

        # Add tools as function declarations
        if tools:
            func_decls = []
            for t in tools:
                func_decls.append(types.FunctionDeclaration(
                    name=t["name"],
                    description=t["description"],
                    parameters=t.get("parameters"),
                ))
            cfg.tools = [types.Tool(function_declarations=func_decls)]

        response = await self.client.aio.models.generate_content_stream(
            model=self.model,
            contents=contents,
            config=cfg,
        )

        input_tokens = 0
        output_tokens = 0
        tool_calls: list[ToolCall] = []

        async for chunk in response:
            if chunk.usage_metadata:
                input_tokens = chunk.usage_metadata.prompt_token_count or 0
                output_tokens = chunk.usage_metadata.candidates_token_count or 0

            if chunk.candidates:
                for candidate in chunk.candidates:
                    if candidate.content and candidate.content.parts:
                        for part in candidate.content.parts:
                            if part.text:
                                yield StreamChunk(text=part.text)
                            elif part.function_call:
                                # Gemini doesn't have tool call IDs — generate synthetic ones
                                tc_id = f"gemini_{uuid.uuid4().hex[:8]}"
                                args = dict(part.function_call.args) if part.function_call.args else {}
                                tool_calls.append(ToolCall(
                                    id=tc_id,
                                    name=part.function_call.name,
                                    arguments=args,
                                ))

        if tool_calls:
            yield StreamToolCall(tool_calls=tool_calls)

        yield StreamDone(usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens))

    def format_tool_calls_message(self, text: str, tool_calls: list[ToolCall]) -> dict:
        """Gemini-format: we store as a dict that gets converted to Content in stream()."""
        content = []
        if text:
            content.append({"type": "text", "text": text})
        for tc in tool_calls:
            content.append({
                "type": "tool_use",
                "name": tc.name,
                "input": tc.arguments,
            })
        return {"role": "assistant", "content": content}

    def format_tool_result(self, tool_call_id: str, name: str, result: str) -> dict:
        """Gemini-format tool result."""
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "name": name,
                    "content": result,
                }
            ],
        }
