"""Write tools — write_file, edit_file (require user permission)."""

from __future__ import annotations

import logging
from pathlib import Path

from . import register
from .definitions import ToolDefinition
from .util import resolve_path as _resolve_path, is_allowed as _is_allowed

log = logging.getLogger("conduit.tools.write")


async def _write_file(path: str, content: str) -> str:
    """Create or overwrite a file."""
    p = _resolve_path(path)
    if not _is_allowed(p):
        return f"Error: Access denied — {path} is not in an allowed directory"

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return f"Successfully wrote {len(content)} bytes to {p}"
    except PermissionError:
        return f"Error: Permission denied writing {path}"
    except Exception as e:
        return f"Error writing {path}: {e}"


async def _edit_file(path: str, old_text: str, new_text: str) -> str:
    """Search and replace text within a file."""
    p = _resolve_path(path)
    if not p.exists():
        return f"Error: File not found: {path}"
    if not p.is_file():
        return f"Error: Not a file: {path}"
    if not _is_allowed(p):
        return f"Error: Access denied — {path} is not in an allowed directory"

    try:
        content = p.read_text()
    except PermissionError:
        return f"Error: Permission denied reading {path}"

    if old_text not in content:
        return f"Error: old_text not found in {path}"

    count = content.count(old_text)
    new_content = content.replace(old_text, new_text, 1)
    p.write_text(new_content)
    return f"Replaced 1 occurrence in {p} ({count} total found, replaced first)"


def register_all():
    """Register write tools."""
    register(ToolDefinition(
        name="write_file",
        description="Create or overwrite a file with the given content. Requires user permission.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or ~-relative path for the file",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file",
                },
            },
            "required": ["path", "content"],
        },
        handler=_write_file,
        permission="write",
    ))

    register(ToolDefinition(
        name="edit_file",
        description="Search and replace text within an existing file. Requires user permission.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to edit",
                },
                "old_text": {
                    "type": "string",
                    "description": "The exact text to find and replace",
                },
                "new_text": {
                    "type": "string",
                    "description": "The replacement text",
                },
            },
            "required": ["path", "old_text", "new_text"],
        },
        handler=_edit_file,
        permission="write",
    ))
