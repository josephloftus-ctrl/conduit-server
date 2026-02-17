"""Agent loop — stream with tool calling, execute tools, feed results back."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from . import config, db
from .models.base import StreamChunk, StreamDone, StreamToolCall, ToolCall, Usage
from .tools import get_tool

if TYPE_CHECKING:
    from .models.base import BaseProvider
    from .tools.definitions import ToolDefinition
    from .ws import ConnectionManager
    from fastapi import WebSocket

log = logging.getLogger("conduit.agent")


async def _is_auto_approve() -> bool:
    """Check if tool permissions are auto-approved (runtime toggle > config default)."""
    override = await db.kv_get("auto_approve_tools")
    if override is not None:
        return override == "true"
    return config.AUTO_APPROVE_ALL


async def _execute_tool(tool: ToolDefinition, tool_call: ToolCall,
                        ws: "WebSocket", manager: "ConnectionManager") -> str:
    """Execute a single tool call with permission checks and WS notifications."""
    # Send tool_start
    await manager.send_tool_start(ws, tool_call.id, tool_call.name, tool_call.arguments)

    # Permission check for write/execute tools
    if tool.permission != "none":
        # Check if auto-approve is active (runtime toggle or config setting)
        auto = await _is_auto_approve()
        if not auto:
            granted = await manager.request_permission(
                ws,
                action=f"{tool.permission}:{tool_call.name}",
                detail=tool_call.arguments,
            )
            if not granted:
                result = "Permission denied by user."
                await manager.send_tool_done(ws, tool_call.id, tool_call.name, error=result)
                return result

    # Execute
    try:
        result = await tool.handler(**tool_call.arguments)
    except Exception as e:
        result = f"Error executing {tool_call.name}: {e}"
        log.error("Tool %s failed: %s", tool_call.name, e, exc_info=True)
        await manager.send_tool_done(ws, tool_call.id, tool_call.name, error=result)
        return result

    await manager.send_tool_done(ws, tool_call.id, tool_call.name, result=result)
    return result


async def run_agent_loop(
    messages: list[dict],
    system: str,
    provider: "BaseProvider",
    tools: list["ToolDefinition"],
    ws: "WebSocket",
    manager: "ConnectionManager",
    max_turns: int = 10,
) -> tuple[str, Usage]:
    """Run the agent loop: stream → detect tool calls → execute → feed back → loop.

    Returns (accumulated_text, total_usage).
    """
    # Convert tools to provider format based on provider class
    from .models.anthropic import AnthropicProvider
    from .models.gemini import GeminiProvider

    if isinstance(provider, AnthropicProvider):
        tool_defs = [t.to_anthropic() for t in tools]
    elif isinstance(provider, GeminiProvider):
        tool_defs = [t.to_gemini() for t in tools]
    else:
        # OpenAI-compat (NIM, Ollama)
        tool_defs = [t.to_openai() for t in tools]

    # Build local name→tool map so dynamically-injected tools (e.g. agent comms)
    # are found even if they aren't in the global registry.
    local_tools = {t.name: t for t in tools}

    total_usage = Usage()
    full_text = ""
    turns = 0

    # Dispatch before_agent_start hook
    try:
        from .plugins import dispatch_hook
        hook_result = await dispatch_hook(
            "before_agent_start", messages=messages, system_prompt=system
        )
        if hook_result and "system_prompt" in hook_result:
            system = hook_result["system_prompt"]
    except Exception:
        pass

    while turns < max_turns:
        turns += 1
        turn_text = ""
        turn_tool_calls: list[ToolCall] = []
        turn_usage = Usage()

        # Stream from provider
        async for item in provider.stream(messages, system=system, tools=tool_defs):
            if isinstance(item, StreamChunk):
                turn_text += item.text
                await manager.send_chunk(ws, item.text)
            elif isinstance(item, StreamToolCall):
                turn_tool_calls = item.tool_calls
            elif isinstance(item, StreamDone):
                turn_usage = item.usage

        total_usage.input_tokens += turn_usage.input_tokens
        total_usage.output_tokens += turn_usage.output_tokens
        full_text += turn_text

        # No tool calls — we're done
        if not turn_tool_calls:
            break

        # Execute tool calls and build continuation messages
        # First, append the assistant message with tool calls
        assistant_msg = provider.format_tool_calls_message(turn_text, turn_tool_calls)
        messages.append(assistant_msg)

        # Execute each tool and append results
        for tc in turn_tool_calls:
            tool = get_tool(tc.name) or local_tools.get(tc.name)
            if not tool:
                result = f"Error: Unknown tool '{tc.name}'"
                await manager.send_tool_done(ws, tc.id, tc.name, error=result)
            else:
                result = await _execute_tool(tool, tc, ws, manager)

                # Dispatch after_tool_call hook
                try:
                    from .plugins import dispatch_hook as _dispatch_hook
                    hook_result = await _dispatch_hook(
                        "after_tool_call", tool_name=tc.name,
                        args=tc.arguments, result=result
                    )
                    if hook_result and "result" in hook_result:
                        result = hook_result["result"]
                except Exception:
                    pass

            result_msg = provider.format_tool_result(tc.id, tc.name, result)
            messages.append(result_msg)

        log.info("Agent turn %d/%d: %d tool call(s)", turns, max_turns,
                 len(turn_tool_calls))

    if turns >= max_turns and turn_tool_calls:
        # Max turns exhausted while still calling tools — ask model to summarize
        messages.append({"role": "user", "content": (
            "You've reached the maximum number of tool calls. "
            "Please summarize what you've found so far and respond to the user."
        )})
        async for item in provider.stream(messages, system=system):
            if isinstance(item, StreamChunk):
                full_text += item.text
                await manager.send_chunk(ws, item.text)
            elif isinstance(item, StreamDone):
                total_usage.input_tokens += item.usage.input_tokens
                total_usage.output_tokens += item.usage.output_tokens

    return full_text, total_usage
