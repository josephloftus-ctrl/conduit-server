"""Tests for markdown skill discovery and parsing."""

import os
from pathlib import Path


def _make_skill(skills_dir: Path, name: str, content: str):
    """Helper: create a skill directory with SKILL.md."""
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content)


def test_parse_skill_md_valid(tmp_skills_dir, sample_skill_md):
    """parse_skill_md should extract frontmatter and body."""
    from server.skills import parse_skill_md
    skill = parse_skill_md(sample_skill_md, "weather")
    assert skill is not None
    assert skill["name"] == "weather"
    assert skill["description"] == "Look up current weather conditions"
    assert "When the user asks about weather" in skill["body"]
    assert skill["agent_affinity"] == "default"


def test_parse_skill_md_no_frontmatter():
    """parse_skill_md should return None for invalid SKILL.md."""
    from server.skills import parse_skill_md
    result = parse_skill_md("# Just markdown, no frontmatter", "bad")
    assert result is None


def test_parse_skill_md_missing_name():
    """parse_skill_md should fall back to folder name if name missing."""
    from server.skills import parse_skill_md
    content = """---
description: Does something
---

# Content here
"""
    skill = parse_skill_md(content, "fallback-name")
    assert skill["name"] == "fallback-name"


def test_discover_skills(tmp_skills_dir, sample_skill_md):
    """discover_skills should find all valid SKILL.md files."""
    from server.skills import discover_skills
    _make_skill(tmp_skills_dir, "weather", sample_skill_md)
    _make_skill(tmp_skills_dir, "bad-skill", "# No frontmatter")
    _make_skill(tmp_skills_dir, "food-order", """---
name: food-order
description: Order food for delivery
---

# Food Order

Help the user order food.
""")
    skills = discover_skills(str(tmp_skills_dir))
    assert len(skills) == 2
    names = {s["name"] for s in skills}
    assert names == {"weather", "food-order"}


def test_discover_skills_empty_dir(tmp_skills_dir):
    """discover_skills should return empty list for empty directory."""
    from server.skills import discover_skills
    skills = discover_skills(str(tmp_skills_dir))
    assert skills == []


def test_discover_skills_missing_dir():
    """discover_skills should return empty list for nonexistent directory."""
    from server.skills import discover_skills
    skills = discover_skills("/tmp/nonexistent-skills-dir-xyz")
    assert skills == []


def test_requires_bins_filtering(tmp_skills_dir):
    """Skills requiring missing binaries should be skipped."""
    from server.skills import discover_skills
    content = """---
name: needs-docker
description: Requires docker
metadata:
  openclaw:
    requires:
      bins: ["docker_unlikely_binary_xyz"]
---

# Needs Docker
"""
    _make_skill(tmp_skills_dir, "needs-docker", content)
    skills = discover_skills(str(tmp_skills_dir))
    assert len(skills) == 0


def test_build_skills_catalog():
    """build_skills_catalog should produce a one-line summary."""
    from server.skills import build_skills_catalog
    skills = [
        {"name": "weather", "description": "Weather lookup"},
        {"name": "food-order", "description": "Order food"},
    ]
    catalog = build_skills_catalog(skills)
    assert "weather" in catalog
    assert "food-order" in catalog
    assert "Weather lookup" in catalog


def test_build_skills_context_no_match():
    """build_skills_context with no keyword match returns catalog only."""
    from server.skills import build_skills_context
    skills = [
        {"name": "weather", "description": "Weather lookup", "body": "# Weather\nLook up weather.", "agent_affinity": None, "requires": {}},
    ]
    context = build_skills_context(skills, "tell me a joke")
    assert "Available skills:" in context
    assert "# Weather" not in context


def test_build_skills_context_keyword_match():
    """build_skills_context with keyword match includes full body."""
    from server.skills import build_skills_context
    skills = [
        {"name": "weather", "description": "Weather lookup", "body": "# Weather\nLook up weather.", "agent_affinity": None, "requires": {}},
    ]
    context = build_skills_context(skills, "what's the weather today")
    assert "# Weather" in context
