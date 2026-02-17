"""Integration tests: skills loaded at startup, injected into prompt."""


def test_skills_context_placeholder_in_system_prompt():
    """System prompt template should contain {skills_context} placeholder."""
    from server import config
    assert "{skills_context}" in config.SYSTEM_PROMPT_TEMPLATE


def test_get_skills_returns_list():
    """get_skills should return a list (even if empty)."""
    from server.skills import get_skills
    result = get_skills()
    assert isinstance(result, list)
