"""Shared test fixtures for Conduit server tests."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure server package is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


@pytest.fixture
def tmp_skills_dir(tmp_path):
    """Create a temporary skills directory with sample skills."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    return skills_dir


@pytest.fixture
def tmp_plugins_dir(tmp_path):
    """Create a temporary plugins directory."""
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    return plugins_dir


@pytest.fixture
def sample_skill_md():
    """Return a valid SKILL.md string."""
    return """---
name: weather
description: Look up current weather conditions
metadata:
  conduit:
    agent: default
---

# Weather

When the user asks about weather, use a web search to find current conditions.
"""


@pytest.fixture
def sample_plugin_manifest():
    """Return a valid plugin.json dict."""
    return {
        "id": "test-plugin",
        "name": "Test Plugin",
        "description": "A test plugin",
        "version": "1.0.0",
    }
