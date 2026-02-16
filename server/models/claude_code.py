"""Claude Code CLI provider — spawns claude as a subprocess, parses stream-json."""

import asyncio
import json
import logging
import os
import shutil

from .base import BaseProvider, StreamChunk, StreamDone, Usage

log = logging.getLogger("conduit.claude_code")


class ClaudeCodeProvider(BaseProvider):
    """Provider that pipes prompts through the Claude Code CLI.

    Claude Code manages its own tools (file reads, edits, bash, etc.),
    so Conduit just displays what it's doing rather than running an agent loop.
    """

    manages_own_tools = True

    def __init__(self, name: str, model: str = "sonnet",
                 working_dir: str = "~", max_budget_usd: float = 0,
                 timeout: int = 600):
        self.name = name
        self.model = model
        self.working_dir = os.path.expanduser(working_dir)
        self.max_budget_usd = max_budget_usd
        self.timeout = timeout

    @property
    def supports_tools(self) -> bool:
        return False

    async def stream(self, messages, system="", tools=None):
        raise NotImplementedError("Use run() for ClaudeCodeProvider")

    async def run(self, prompt: str, session_id: str | None,
                  ws, manager) -> tuple[str, Usage, str | None, float]:
        """Spawn claude CLI, parse stream-json, emit WS messages.

        Returns (full_text, usage, session_id, cost_usd).
        """
        claude_bin = shutil.which("claude")
        if not claude_bin:
            raise RuntimeError("claude CLI not found in PATH")

        cmd = [
            claude_bin, "-p",
            "--output-format", "stream-json",
            "--verbose",
        ]

        if session_id:
            cmd.extend(["--resume", session_id])

        if self.model:
            cmd.extend(["--model", self.model])

        if self.max_budget_usd > 0:
            cmd.extend(["--max-budget-usd", str(self.max_budget_usd)])

        # Append prompt as CLI argument
        cmd.append(prompt)

        log.info("Spawning claude CLI (session=%s)", session_id or "new")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.working_dir,
            limit=1024 * 1024,  # 1MB line buffer (default 64KB too small for large tool results)
        )

        full_text_parts: list[str] = []
        usage = Usage()
        new_session_id: str | None = None
        cost_usd = 0.0
        prev_text_len = 0  # Track text sent so far for delta computation

        try:
            async with asyncio.timeout(self.timeout):
                async for raw_line in proc.stdout:
                    line = raw_line.decode().strip()
                    if not line:
                        continue

                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        log.debug("Non-JSON line: %s", line[:100])
                        continue

                    etype = event.get("type")

                    if etype == "system":
                        if event.get("subtype") == "init":
                            new_session_id = event.get("session_id")
                            log.info("Claude Code session: %s", new_session_id)

                    elif etype == "assistant":
                        msg = event.get("message", {})
                        content_blocks = msg.get("content", [])

                        for block in content_blocks:
                            btype = block.get("type")

                            if btype == "text":
                                text = block.get("text", "")
                                # Compute delta from previously sent text
                                if len(text) > prev_text_len:
                                    delta = text[prev_text_len:]
                                    await manager.send_chunk(ws, delta)
                                    full_text_parts.append(delta)
                                    prev_text_len = len(text)

                            elif btype == "tool_use":
                                tool_id = block.get("id", "")
                                tool_name = block.get("name", "unknown")
                                tool_input = block.get("input", {})
                                await manager.send_tool_start(
                                    ws, tool_id, tool_name, tool_input
                                )
                                # Reset text tracking for next turn
                                prev_text_len = 0

                    elif etype in ("tool", "tool_result"):
                        # Tool result from Claude Code's execution
                        tool_use_id = event.get("tool_use_id", "")
                        tool_name = event.get("name", "")
                        content = event.get("content", "")
                        # Content may be a list of content blocks or a string
                        if isinstance(content, list):
                            parts = []
                            for cb in content:
                                if isinstance(cb, dict) and cb.get("type") == "text":
                                    parts.append(cb.get("text", ""))
                            content = "\n".join(parts)
                        elif not isinstance(content, str):
                            content = str(content)
                        error = ""
                        if event.get("is_error"):
                            error = content
                            content = ""
                        await manager.send_tool_done(
                            ws, tool_use_id, tool_name,
                            result=content, error=error,
                        )

                    elif etype == "result":
                        cost_usd = event.get("total_cost_usd", 0.0)
                        result_usage = event.get("usage", {})
                        usage = Usage(
                            input_tokens=result_usage.get("input_tokens", 0),
                            output_tokens=result_usage.get("output_tokens", 0),
                        )
                        # Capture session_id from result if not set from init
                        if not new_session_id:
                            new_session_id = event.get("session_id")

        except TimeoutError:
            log.warning("Claude Code timed out after %ds", self.timeout)
            proc.kill()
            await proc.wait()
            await manager.send_error(ws, f"Claude Code timed out after {self.timeout}s")
        except asyncio.CancelledError:
            log.info("Claude Code cancelled — killing subprocess")
            proc.kill()
            await proc.wait()
            raise
        else:
            await proc.wait()
            if proc.returncode and proc.returncode != 0:
                stderr = ""
                if proc.stderr:
                    stderr = (await proc.stderr.read()).decode().strip()
                log.warning("Claude Code exited %d: %s", proc.returncode, stderr)

        return "".join(full_text_parts), usage, new_session_id, cost_usd
