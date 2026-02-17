"""Markdown skill discovery and parsing (OpenClaw compatible).

Scans a skills directory for SKILL.md files with YAML frontmatter,
parses them into structured dicts, and builds context strings for
injection into the agent system prompt.

Directory layout expected::

    skills_dir/
        weather/
            SKILL.md
        food-order/
            SKILL.md
"""

import logging
import re
import shutil
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level skill store
# ---------------------------------------------------------------------------
_skills: list[dict] = []


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_skill_md(content: str, folder_name: str) -> Optional[dict]:
    """Parse a SKILL.md string into a skill dict.

    Expected format::

        ---
        name: weather
        description: Look up current weather conditions
        metadata:
          conduit:
            agent: default
          openclaw:
            requires:
              bins: ["curl"]
        ---

        # Weather
        When the user asks about weather ...

    Returns None if the content lacks valid YAML frontmatter.
    """
    # Match frontmatter between --- delimiters at the top of the file
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)", content, re.DOTALL)
    if not match:
        return None

    frontmatter_raw = match.group(1)
    body = match.group(2).strip()

    try:
        frontmatter = yaml.safe_load(frontmatter_raw)
    except yaml.YAMLError:
        log.warning("Invalid YAML frontmatter in skill '%s'", folder_name)
        return None

    if not isinstance(frontmatter, dict):
        return None

    # Extract fields with fallbacks
    name = frontmatter.get("name", folder_name)
    description = frontmatter.get("description", "")
    metadata = frontmatter.get("metadata", {})

    # Agent affinity: check conduit.agent in metadata
    conduit_meta = metadata.get("conduit", {}) if isinstance(metadata, dict) else {}
    agent_affinity = conduit_meta.get("agent") if isinstance(conduit_meta, dict) else None

    # Requires: check openclaw.requires in metadata
    openclaw_meta = metadata.get("openclaw", {}) if isinstance(metadata, dict) else {}
    requires = openclaw_meta.get("requires", {}) if isinstance(openclaw_meta, dict) else {}

    return {
        "name": name,
        "description": description,
        "body": body,
        "agent_affinity": agent_affinity,
        "requires": requires if isinstance(requires, dict) else {},
        "metadata": metadata,
    }


# ---------------------------------------------------------------------------
# Binary requirement checking
# ---------------------------------------------------------------------------

def _check_bins(requires: dict) -> bool:
    """Check that all required binaries are available on PATH.

    Returns True if all bins are found (or no bins required), False otherwise.
    """
    bins = requires.get("bins", [])
    if not isinstance(bins, list):
        return True
    for binary in bins:
        if shutil.which(binary) is None:
            log.debug("Skill requires missing binary: %s", binary)
            return False
    return True


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_skills(skills_dir: str) -> list[dict]:
    """Scan a directory for valid SKILL.md files and return parsed skills.

    Each subdirectory of *skills_dir* is expected to contain a ``SKILL.md``
    file.  Directories without one (or with invalid frontmatter) are skipped.
    Skills that require missing binaries are also filtered out.

    Returns an empty list if the directory does not exist or is empty.
    """
    skills_path = Path(skills_dir)
    if not skills_path.is_dir():
        return []

    skills = []
    for child in sorted(skills_path.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            content = skill_md.read_text(encoding="utf-8")
        except OSError as exc:
            log.warning("Could not read %s: %s", skill_md, exc)
            continue

        skill = parse_skill_md(content, child.name)
        if skill is None:
            log.debug("Skipping %s — invalid SKILL.md", child.name)
            continue

        # Check binary requirements
        if not _check_bins(skill.get("requires", {})):
            log.info("Skipping skill '%s' — missing required binaries", skill["name"])
            continue

        skills.append(skill)

    return skills


# ---------------------------------------------------------------------------
# Catalog & context building
# ---------------------------------------------------------------------------

def build_skills_catalog(skills: list[dict]) -> str:
    """Build a one-line-per-skill catalog string.

    Example output::

        Available skills:
        - weather: Weather lookup
        - food-order: Order food
    """
    if not skills:
        return ""
    lines = ["Available skills:"]
    for s in skills:
        lines.append(f"- {s['name']}: {s['description']}")
    return "\n".join(lines)


def build_skills_context(
    skills: list[dict],
    user_message: str,
    max_injected: int = 2,
) -> str:
    """Build context string: catalog + keyword-matched full skill bodies.

    Keyword matching: a skill matches if any word from the skill name
    appears in the user message (case-insensitive).  Up to *max_injected*
    matched skill bodies are appended after the catalog.
    """
    catalog = build_skills_catalog(skills)
    if not catalog:
        return ""

    # Keyword matching — check if any word from the skill name appears
    # in the user message.
    msg_lower = user_message.lower()
    matched = []
    for s in skills:
        name_words = s["name"].replace("-", " ").split()
        if any(word.lower() in msg_lower for word in name_words):
            matched.append(s)
        if len(matched) >= max_injected:
            break

    if not matched:
        return catalog

    parts = [catalog, ""]
    for s in matched:
        parts.append(f"--- Skill: {s['name']} ---")
        parts.append(s["body"])
        parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Module-level store helpers
# ---------------------------------------------------------------------------

def load_skills(skills_dir: str) -> list[dict]:
    """Discover skills and store them in the module-level ``_skills`` list.

    Returns the loaded skills list.
    """
    global _skills
    _skills = discover_skills(skills_dir)
    log.info("Loaded %d markdown skills from %s", len(_skills), skills_dir)
    return _skills


def get_skills() -> list[dict]:
    """Return the currently loaded skills."""
    return _skills


def get_skills_context(user_message: str, max_per_turn: int = 2) -> str:
    """Convenience wrapper: build context from the loaded skills store."""
    return build_skills_context(_skills, user_message, max_injected=max_per_turn)
