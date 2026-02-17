"""Tests for new config sections: markdown_skills, plugins, subagents."""


def test_config_has_markdown_skills_section():
    """Config should expose MARKDOWN_SKILLS_* constants."""
    from server import config
    assert hasattr(config, "MARKDOWN_SKILLS_ENABLED")
    assert hasattr(config, "MARKDOWN_SKILLS_DIR")
    assert hasattr(config, "MARKDOWN_SKILLS_MAX_PER_TURN")


def test_config_has_plugins_section():
    """Config should expose PLUGINS_* constants."""
    from server import config
    assert hasattr(config, "PLUGINS_ENABLED")
    assert hasattr(config, "PLUGINS_DIR")


def test_config_has_subagents_section():
    """Config should expose SUBAGENTS_* constants."""
    from server import config
    assert hasattr(config, "SUBAGENTS_ENABLED")
    assert hasattr(config, "SUBAGENTS_MAX_SPAWN_DEPTH")
    assert hasattr(config, "SUBAGENTS_MAX_CHILDREN")
    assert hasattr(config, "SUBAGENTS_DEFAULT_TIMEOUT")
    assert hasattr(config, "SUBAGENTS_SESSION_TTL_MINUTES")


def test_config_defaults_are_sensible():
    """Default values should be usable without config.yaml changes."""
    from server import config
    assert config.MARKDOWN_SKILLS_ENABLED is True
    assert "skills" in config.MARKDOWN_SKILLS_DIR
    assert config.MARKDOWN_SKILLS_MAX_PER_TURN == 2
    assert config.PLUGINS_ENABLED is True
    assert "plugins" in config.PLUGINS_DIR
    assert config.SUBAGENTS_ENABLED is True
    assert config.SUBAGENTS_MAX_SPAWN_DEPTH == 2
    assert config.SUBAGENTS_MAX_CHILDREN == 5
    assert config.SUBAGENTS_DEFAULT_TIMEOUT == 300
    assert config.SUBAGENTS_SESSION_TTL_MINUTES == 60
