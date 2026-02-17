"""Smoke tests — validate all new modules import and integrate correctly."""


def test_skills_module_imports():
    """server.skills should import without errors."""
    from server.skills import (
        discover_skills, parse_skill_md, build_skills_catalog,
        build_skills_context, load_skills, get_skills, get_skills_context,
        skill_install,
    )


def test_plugins_module_imports():
    """server.plugins should import without errors."""
    from server.plugins import (
        PluginAPI, discover_plugins, load_plugin, load_all_plugins,
        dispatch_hook, get_loaded_plugins,
    )


def test_subagents_module_imports():
    """server.subagents should import without errors."""
    from server.subagents import (
        SubagentSession, SessionRegistry, init_registry, get_registry,
        build_session_tools, queue_announcement, drain_announcements,
        init_db, persist_session, run_subagent,
    )


def test_config_has_all_new_sections():
    """All new config constants should be accessible."""
    from server import config
    # Markdown skills
    assert isinstance(config.MARKDOWN_SKILLS_ENABLED, bool)
    assert isinstance(config.MARKDOWN_SKILLS_DIR, str)
    assert isinstance(config.MARKDOWN_SKILLS_MAX_PER_TURN, int)
    # Plugins
    assert isinstance(config.PLUGINS_ENABLED, bool)
    assert isinstance(config.PLUGINS_DIR, str)
    # Subagents
    assert isinstance(config.SUBAGENTS_ENABLED, bool)
    assert isinstance(config.SUBAGENTS_MAX_SPAWN_DEPTH, int)
    assert isinstance(config.SUBAGENTS_MAX_CHILDREN, int)
    assert isinstance(config.SUBAGENTS_DEFAULT_TIMEOUT, int)
    assert isinstance(config.SUBAGENTS_SESSION_TTL_MINUTES, int)


def test_system_prompt_has_skills_placeholder():
    """The system prompt template should include {skills_context}."""
    from server import config
    assert "{skills_context}" in config.SYSTEM_PROMPT_TEMPLATE


def test_tool_registry_type():
    """Tool registry should be a dict of ToolDefinitions."""
    from server.tools import _TOOLS
    assert isinstance(_TOOLS, dict)
