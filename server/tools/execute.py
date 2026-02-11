"""Execute tool — run shell commands (requires user permission)."""

from __future__ import annotations

import asyncio
import logging

from . import register
from .definitions import ToolDefinition
from .. import config

log = logging.getLogger("conduit.tools.exec")

MAX_OUTPUT = 50 * 1024


async def _run_command(command: str, cwd: str = "", timeout: int = 0) -> str:
    """Run a shell command and return stdout+stderr."""
    effective_timeout = timeout or config.COMMAND_TIMEOUT
    effective_cwd = cwd or None

    proc = None
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=effective_cwd,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=effective_timeout
        )
    except asyncio.TimeoutError:
        if proc:
            proc.kill()
            await proc.wait()
        return f"Error: Command timed out after {effective_timeout}s"
    except Exception as e:
        if proc and proc.returncode is None:
            proc.kill()
            await proc.wait()
        return f"Error running command: {e}"

    output = stdout.decode(errors="replace")
    err = stderr.decode(errors="replace")

    result_parts = []
    if output:
        result_parts.append(output)
    if err:
        result_parts.append(f"[stderr]\n{err}")
    if proc.returncode != 0:
        result_parts.append(f"[exit code: {proc.returncode}]")

    result = "\n".join(result_parts) if result_parts else "(no output)"

    if len(result) > MAX_OUTPUT:
        result = result[:MAX_OUTPUT] + "\n... [truncated]"

    return result


def register_all():
    """Register execute tools."""
    register(ToolDefinition(
        name="run_command",
        description="Run a shell command and return its output. Requires user permission. Use for system commands, file operations, etc.",
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory. Optional.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds. Optional, defaults to config value.",
                },
            },
            "required": ["command"],
        },
        handler=_run_command,
        permission="execute",
    ))
