"""Tests for the skill_install tool."""

import pytest


@pytest.mark.asyncio
async def test_skill_install_from_url(tmp_skills_dir, monkeypatch):
    """skill_install should download and save a SKILL.md from a URL."""
    from server import skills

    # Mock the HTTP fetch
    skill_content = """---
name: test-remote
description: A remote skill
---

# Test Remote Skill

Do the remote thing.
"""

    async def mock_fetch(url, timeout=15):
        return skill_content

    monkeypatch.setattr(skills, "_fetch_url", mock_fetch)
    monkeypatch.setattr(skills, "_skills_dir", str(tmp_skills_dir))

    result = await skills.skill_install(name="test-remote", source="https://example.com/SKILL.md")
    assert "Installed" in result or "installed" in result.lower()

    # Verify file was written
    skill_path = tmp_skills_dir / "test-remote" / "SKILL.md"
    assert skill_path.exists()
    assert "test-remote" in skill_path.read_text()


@pytest.mark.asyncio
async def test_skill_install_invalid_content(tmp_skills_dir, monkeypatch):
    """skill_install should reject content without valid frontmatter."""
    from server import skills

    async def mock_fetch(url, timeout=15):
        return "# Just markdown, no frontmatter"

    monkeypatch.setattr(skills, "_fetch_url", mock_fetch)
    monkeypatch.setattr(skills, "_skills_dir", str(tmp_skills_dir))

    result = await skills.skill_install(name="bad-skill", source="https://example.com/bad")
    assert "Error" in result or "error" in result.lower() or "invalid" in result.lower()
