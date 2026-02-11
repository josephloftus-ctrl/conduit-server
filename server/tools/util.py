"""Shared utilities for tool implementations."""

import os
from pathlib import Path

from .. import config


def resolve_path(path_str: str) -> Path:
    """Expand ~ and resolve to absolute path."""
    return Path(os.path.expanduser(path_str)).resolve()


def is_allowed(path: Path) -> bool:
    """Check if path is within an allowed directory."""
    allowed = getattr(config, "ALLOWED_DIRECTORIES", [])
    for d in allowed:
        allowed_path = resolve_path(d)
        try:
            path.relative_to(allowed_path)
            return True
        except ValueError:
            continue
    return False
