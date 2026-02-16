"""Project indexer — scans configured project directories and writes compact YAML indexes."""

import logging
import os
import re
from datetime import datetime
from pathlib import Path

import yaml

from . import config

log = logging.getLogger("conduit.indexer")

# Directories to skip entirely
SKIP_DIRS = {
    "node_modules", ".venv", "venv", "__pycache__", ".git", "dist", "build",
    ".next", ".svelte-kit", ".nuxt", ".cache", ".tox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "egg-info", ".eggs", "htmlcov",
    "coverage", ".turbo", ".parcel-cache",
}

# File extensions to skip
SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".o", ".a", ".dylib", ".dll", ".exe",
    ".whl", ".egg", ".tar", ".gz", ".zip", ".bz2", ".xz",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
    ".woff", ".woff2", ".ttf", ".eot",
    ".lock", ".map",
}

# Code file extensions we extract descriptions from
CODE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".svelte", ".vue",
    ".yaml", ".yml", ".toml", ".json", ".md", ".sh", ".bash",
    ".go", ".rs", ".rb", ".java", ".kt", ".swift", ".c", ".cpp", ".h",
}

MAX_DEPTH = 4
LARGE_FILE_THRESHOLD = 10 * 1024  # 10KB


def _extract_description(filepath: Path) -> str:
    """Extract a one-line description from a code file.

    For Python: first docstring or first comment.
    For JS/TS: first // or /* comment.
    For YAML/TOML: first # comment.
    For Markdown: first heading.
    """
    try:
        text = filepath.read_text(errors="replace")[:2000]  # Only read first 2KB
    except (PermissionError, OSError):
        return ""

    ext = filepath.suffix.lower()

    if ext == ".py":
        # Try module docstring
        m = re.match(r'^(?:#.*\n)*\s*(?:"""(.*?)"""|\'\'\'(.*?)\'\'\')', text, re.DOTALL)
        if m:
            doc = (m.group(1) or m.group(2) or "").strip()
            first_line = doc.split("\n")[0].strip()
            if first_line:
                return first_line[:120]
        # Fall back to first comment
        for line in text.split("\n")[:10]:
            line = line.strip()
            if line.startswith("#") and not line.startswith("#!"):
                return line.lstrip("# ").strip()[:120]

    elif ext in (".ts", ".tsx", ".js", ".jsx", ".svelte", ".vue"):
        for line in text.split("\n")[:10]:
            line = line.strip()
            if line.startswith("//"):
                return line.lstrip("/ ").strip()[:120]
            if line.startswith("/*"):
                comment = line.lstrip("/* ").rstrip("*/").strip()
                if comment:
                    return comment[:120]

    elif ext in (".yaml", ".yml", ".toml", ".sh", ".bash"):
        for line in text.split("\n")[:5]:
            line = line.strip()
            if line.startswith("#") and not line.startswith("#!"):
                return line.lstrip("# ").strip()[:120]

    elif ext == ".md":
        for line in text.split("\n")[:5]:
            line = line.strip()
            if line.startswith("#"):
                return line.lstrip("# ").strip()[:120]

    return ""


def _scan_directory(dirpath: Path, depth: int = 0) -> dict:
    """Recursively scan a directory and build a structure tree.

    Returns a dict with keys being file/dir names and values being
    either a description string (for files) or a nested dict (for dirs).
    """
    result = {}

    try:
        entries = sorted(dirpath.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except (PermissionError, OSError):
        return result

    for entry in entries:
        name = entry.name

        # Skip hidden files/dirs (except specific ones)
        if name.startswith(".") and name not in (".env.example", ".gitignore"):
            continue

        if entry.is_dir():
            if name in SKIP_DIRS or name.endswith(".egg-info"):
                continue

            if depth >= MAX_DEPTH:
                # Just count files instead of recursing
                try:
                    count = sum(1 for _ in entry.rglob("*") if _.is_file())
                    result[name + "/"] = f"({count} files, depth limit)"
                except (PermissionError, OSError):
                    result[name + "/"] = "(access denied)"
                continue

            subtree = _scan_directory(entry, depth + 1)
            if subtree:
                result[name + "/"] = subtree

        elif entry.is_file():
            ext = entry.suffix.lower()
            if ext in SKIP_EXTENSIONS:
                continue

            info_parts = []

            # Get description for code files
            if ext in CODE_EXTENSIONS:
                desc = _extract_description(entry)
                if desc:
                    info_parts.append(desc)

            # Note large files
            try:
                size = entry.stat().st_size
                if size > LARGE_FILE_THRESHOLD:
                    if size >= 1024 * 1024:
                        info_parts.append(f"{size / (1024*1024):.1f}MB")
                    else:
                        info_parts.append(f"{size / 1024:.0f}KB")
            except OSError:
                pass

            result[name] = " | ".join(info_parts) if info_parts else ""

    return result


async def index_project(name: str, path: str) -> str:
    """Scan a single project and write its index to YAML.

    Returns the output file path.
    """
    project_path = Path(os.path.expanduser(path)).resolve()
    if not project_path.exists():
        log.warning("Project path does not exist: %s", project_path)
        return ""

    output_dir = Path(os.path.expanduser(config.INDEXER_OUTPUT_DIR))
    output_dir.mkdir(parents=True, exist_ok=True)

    log.info("Indexing project '%s' at %s", name, project_path)

    structure = _scan_directory(project_path)

    index_data = {
        "project": name,
        "path": str(project_path),
        "scanned": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "structure": structure,
    }

    output_path = output_dir / f"{name}.yaml"
    output_path.write_text(
        yaml.dump(index_data, default_flow_style=False, sort_keys=False, width=120)
    )

    log.info("Index written: %s", output_path)
    return str(output_path)


async def index_all():
    """Scan all configured projects and write indexes."""
    projects = config.INDEXER_PROJECTS
    if not projects:
        log.info("No projects configured for indexing")
        return

    log.info("Starting project indexing for %d projects", len(projects))

    for proj in projects:
        try:
            await index_project(proj["name"], proj["path"])
        except Exception as e:
            log.error("Failed to index project '%s': %s", proj.get("name", "?"), e)

    log.info("Project indexing complete")
