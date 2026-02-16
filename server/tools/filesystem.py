"""Filesystem tools — read_file, list_directory, glob_files, grep, update_index."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

import yaml

from . import register
from .definitions import ToolDefinition
from .util import resolve_path as _resolve_path, is_allowed as _is_allowed

log = logging.getLogger("conduit.tools.fs")

INDEX_DIR = Path.home() / ".index" / "domains"

MAX_FILE_SIZE = 50 * 1024  # 50KB truncation limit
MAX_RESULTS = 200


async def _read_file(path: str, offset: int = 0, limit: int = 0) -> str:
    """Read file contents with optional line offset/limit."""
    p = _resolve_path(path)
    if not p.exists():
        return f"Error: File not found: {path}"
    if not p.is_file():
        return f"Error: Not a file: {path}"
    if not _is_allowed(p):
        return f"Error: Access denied — {path} is not in an allowed directory"

    try:
        content = p.read_text(errors="replace")
    except PermissionError:
        return f"Error: Permission denied reading {path}"

    # Apply line offset/limit
    if offset > 0 or limit > 0:
        lines = content.splitlines(keepends=True)
        start = max(0, offset)
        end = start + limit if limit > 0 else len(lines)
        content = "".join(lines[start:end])

    if len(content) > MAX_FILE_SIZE:
        content = content[:MAX_FILE_SIZE] + f"\n\n... [truncated at {MAX_FILE_SIZE // 1024}KB]"

    return content


async def _list_directory(path: str) -> str:
    """List directory entries with type indicators."""
    p = _resolve_path(path)
    if not p.exists():
        return f"Error: Directory not found: {path}"
    if not p.is_dir():
        return f"Error: Not a directory: {path}"
    if not _is_allowed(p):
        return f"Error: Access denied — {path} is not in an allowed directory"

    try:
        entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except PermissionError:
        return f"Error: Permission denied listing {path}"

    lines = []
    for entry in entries[:MAX_RESULTS]:
        try:
            if entry.is_symlink():
                lines.append(f"  {entry.name} -> {entry.resolve()}")
            elif entry.is_dir():
                lines.append(f"  {entry.name}/")
            else:
                size = entry.stat().st_size
                if size >= 1024 * 1024:
                    size_str = f"{size / (1024*1024):.1f}MB"
                elif size >= 1024:
                    size_str = f"{size / 1024:.1f}KB"
                else:
                    size_str = f"{size}B"
                lines.append(f"  {entry.name}  ({size_str})")
        except (PermissionError, OSError):
            lines.append(f"  {entry.name}  (access denied)")

    header = f"{p}/ ({len(entries)} entries)"
    if len(entries) > MAX_RESULTS:
        header += f" [showing first {MAX_RESULTS}]"
    return header + "\n" + "\n".join(lines)


async def _glob_files(pattern: str, path: str = "~") -> str:
    """Find files matching a glob pattern."""
    p = _resolve_path(path)
    if not _is_allowed(p):
        return f"Error: Access denied — {path} is not in an allowed directory"
    if not p.exists():
        return f"Error: Path not found: {path}"

    matches = []
    for match in p.glob(pattern):
        matches.append(str(match))
        if len(matches) >= MAX_RESULTS:
            break

    if not matches:
        return f"No files matching '{pattern}' in {p}"

    result = f"Found {len(matches)} match(es) for '{pattern}' in {p}:\n"
    result += "\n".join(f"  {m}" for m in sorted(matches))
    if len(matches) >= MAX_RESULTS:
        result += f"\n  ... [limited to {MAX_RESULTS} results]"
    return result


async def _grep(pattern: str, path: str = "~", include: str = "") -> str:
    """Search file contents with regex using grep."""
    p = _resolve_path(path)
    if not _is_allowed(p):
        return f"Error: Access denied — {path} is not in an allowed directory"
    if not p.exists():
        return f"Error: Path not found: {path}"

    cmd = ["grep", "-rn", "--color=never", "-m", "50"]
    if include:
        cmd.extend(["--include", include])
    cmd.extend([pattern, str(p)])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
    except asyncio.TimeoutError:
        return "Error: Search timed out after 15 seconds"

    output = stdout.decode(errors="replace").strip()
    if not output:
        return f"No matches for '{pattern}' in {p}"

    # Truncate if too large
    if len(output) > MAX_FILE_SIZE:
        output = output[:MAX_FILE_SIZE] + "\n... [truncated]"

    return output


async def _update_index(action: str, domain: str, path: str, summary: str = "") -> str:
    """Add, update, or remove an entry in a domain YAML index file."""
    yaml_path = INDEX_DIR / f"{domain}.yaml"
    if not yaml_path.exists():
        return f"Error: Domain file not found: {domain}.yaml"

    try:
        data = yaml.safe_load(yaml_path.read_text()) or {}
    except Exception as e:
        return f"Error reading {domain}.yaml: {e}"

    files = data.setdefault("files", {})

    if action == "add" or action == "update":
        if not summary:
            return "Error: summary is required for add/update"
        ext = Path(path).suffix.lstrip(".")
        files[path] = {
            "summary": summary,
            "type": ext,
            "added": datetime.now().strftime("%Y-%m-%d"),
        }
    elif action == "remove":
        if path in files:
            del files[path]
        else:
            return f"Entry not found: {path}"
    else:
        return f"Error: Unknown action '{action}'. Use add, update, or remove."

    data["updated"] = datetime.now().strftime("%Y-%m-%d")

    try:
        yaml_path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    except Exception as e:
        return f"Error writing {domain}.yaml: {e}"

    return f"OK: {action}d '{path}' in {domain}.yaml"


async def _load_project_index(project: str) -> str:
    """Load a pre-computed project index."""
    from .. import config
    output_dir = Path(os.path.expanduser(config.INDEXER_OUTPUT_DIR))
    index_path = output_dir / f"{project}.yaml"
    if not index_path.exists():
        available = [p.stem for p in output_dir.glob("*.yaml")] if output_dir.exists() else []
        return f"No index found for '{project}'. Available: {', '.join(available) or 'none (run indexer first)'}"
    content = index_path.read_text()
    if len(content) > 50000:
        content = content[:50000] + "\n... (truncated)"
    return content


# --- Register all filesystem tools ---

def register_all():
    """Register all filesystem tools."""
    register(ToolDefinition(
        name="read_file",
        description="Read the contents of a file. Returns the text content. Use offset/limit for large files.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or ~-relative path to the file",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line number to start reading from (0-indexed). Optional.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read. Optional, 0 means all.",
                },
            },
            "required": ["path"],
        },
        handler=_read_file,
        permission="none",
    ))

    register(ToolDefinition(
        name="list_directory",
        description="List the contents of a directory. Shows files with sizes and subdirectories.",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or ~-relative path to the directory",
                },
            },
            "required": ["path"],
        },
        handler=_list_directory,
        permission="none",
    ))

    register(ToolDefinition(
        name="glob_files",
        description="Find files matching a glob pattern (e.g. '**/*.pdf', '*.xlsx'). Searches recursively.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match (e.g. '**/*.pdf', '*.py')",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search in. Defaults to ~.",
                },
            },
            "required": ["pattern"],
        },
        handler=_glob_files,
        permission="none",
    ))

    register(ToolDefinition(
        name="grep",
        description="Search for a regex pattern in file contents. Returns matching lines with file paths and line numbers.",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for",
                },
                "path": {
                    "type": "string",
                    "description": "Directory or file to search in. Defaults to ~.",
                },
                "include": {
                    "type": "string",
                    "description": "File glob to filter (e.g. '*.py', '*.md'). Optional.",
                },
            },
            "required": ["pattern"],
        },
        handler=_grep,
        permission="none",
    ))

    register(ToolDefinition(
        name="update_index",
        description="Add, update, or remove a file entry in the ~/.index/ domain YAML files. Use when creating, moving, or deleting files.",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "update", "remove"],
                    "description": "Action to perform: add, update, or remove an entry",
                },
                "domain": {
                    "type": "string",
                    "enum": ["work-lockheed", "work-general", "projects"],
                    "description": "Domain YAML file to update",
                },
                "path": {
                    "type": "string",
                    "description": "File path relative to the domain base (e.g. 'sales/2-5-26.pdf')",
                },
                "summary": {
                    "type": "string",
                    "description": "One-sentence description of the file (required for add/update)",
                },
            },
            "required": ["action", "domain", "path"],
        },
        handler=_update_index,
        permission="write",
    ))

    register(ToolDefinition(
        name="load_project_index",
        description="Load a pre-computed map of a project's file structure and module descriptions. Use this BEFORE exploring a project with glob/grep — it gives you the full layout in one call.",
        parameters={
            "type": "object",
            "properties": {
                "project": {
                    "type": "string",
                    "description": "Project name (e.g. 'spectre', 'conduit')",
                },
            },
            "required": ["project"],
        },
        handler=_load_project_index,
        permission="none",
    ))
