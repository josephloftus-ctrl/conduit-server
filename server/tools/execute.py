"""Execute tool — run shell commands (requires user permission)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

from . import register
from .definitions import ToolDefinition
from .. import config

log = logging.getLogger("conduit.tools.exec")

MAX_OUTPUT = 50 * 1024

# ---------------------------------------------------------------------------
# Dangerous command blocklist
# ---------------------------------------------------------------------------
# Patterns that should never be executed, even with user approval.
# These target irreversible destructive operations and remote code execution.

BLOCKED_PATTERNS: list[re.Pattern] = [
    # Recursive delete at filesystem root or home root
    re.compile(r"\brm\s+.*-[a-zA-Z]*r[a-zA-Z]*f?\s+/\s*$"),       # rm -rf /
    re.compile(r"\brm\s+.*-[a-zA-Z]*r[a-zA-Z]*f?\s+/\*"),          # rm -rf /*
    re.compile(r"\brm\s+.*-[a-zA-Z]*r[a-zA-Z]*f?\s+~\s*$"),        # rm -rf ~
    re.compile(r"\brm\s+.*-[a-zA-Z]*r[a-zA-Z]*f?\s+~/\*"),         # rm -rf ~/*
    # Disk/partition destruction
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+.*\bof\s*=\s*/dev/"),                       # dd of=/dev/sda
    re.compile(r"\bshred\b"),
    # Curl/wget piped to shell — remote code execution
    re.compile(r"\bcurl\b.*\|\s*(ba)?sh\b"),
    re.compile(r"\bwget\b.*\|\s*(ba)?sh\b"),
    re.compile(r"\bcurl\b.*\|\s*sudo\b"),
    re.compile(r"\bwget\b.*\|\s*sudo\b"),
    # Fork bomb
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;"),
    # Overwrite critical system files
    re.compile(r">\s*/etc/(passwd|shadow|sudoers)"),
    re.compile(r">\s*/etc/ssh/"),
    # Shutdown/reboot (accidental is annoying on a tablet)
    re.compile(r"\b(shutdown|reboot|poweroff|halt)\b"),
    # Kernel/system manipulation
    re.compile(r"\binsmod\b"),
    re.compile(r"\brmmod\b"),
    re.compile(r"\bmodprobe\b.*--remove"),
]


def check_command(command: str) -> str | None:
    """Check a command against the blocklist.

    Returns an error message if blocked, None if the command is safe.
    """
    cmd_stripped = command.strip()
    for pattern in BLOCKED_PATTERNS:
        if pattern.search(cmd_stripped):
            log.warning("Blocked dangerous command: %s", cmd_stripped[:200])
            return f"Blocked: command matches dangerous pattern ({pattern.pattern})"
    return None


def validate_cwd(cwd: str) -> str | None:
    """Validate that cwd is within allowed directories.

    Returns an error message if blocked, None if allowed.
    Skips validation if ALLOWED_DIRECTORIES is empty (no restrictions).
    """
    allowed = getattr(config, "ALLOWED_DIRECTORIES", [])
    if not allowed:
        return None

    try:
        resolved = Path(os.path.expanduser(cwd)).resolve()
    except (ValueError, OSError):
        return f"Error: invalid working directory '{cwd}'"

    for d in allowed:
        allowed_path = Path(os.path.expanduser(d)).resolve()
        try:
            resolved.relative_to(allowed_path)
            return None
        except ValueError:
            continue

    return f"Error: working directory '{cwd}' is outside allowed directories"


async def _run_command(command: str, cwd: str = "", timeout: int = 0) -> str:
    """Run a shell command and return stdout+stderr."""
    # Check blocklist
    blocked = check_command(command)
    if blocked:
        return f"Error: {blocked}"

    # Validate cwd if provided
    if cwd:
        cwd_error = validate_cwd(cwd)
        if cwd_error:
            return cwd_error

    effective_timeout = timeout or config.COMMAND_TIMEOUT
    effective_cwd = cwd or None

    # Audit log
    log.info("Executing command: %s (cwd=%s, timeout=%s)", command[:200], effective_cwd, effective_timeout)

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
