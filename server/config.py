"""Configuration loader — .env secrets + config.yaml settings."""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

SERVER_DIR = Path(__file__).parent
load_dotenv(SERVER_DIR / ".env")

# Load YAML config
_config_path = SERVER_DIR / "config.yaml"
with open(_config_path) as f:
    _raw = yaml.safe_load(f)

# Server settings
server = _raw.get("server", {})
HOST = server.get("host", "0.0.0.0")
PORT = server.get("port", 8080)

# Personality
personality = _raw.get("personality", {})
PERSONALITY_NAME = personality.get("name", "Conduit")
SYSTEM_PROMPT_TEMPLATE = personality.get("system_prompt", "You are a helpful AI assistant.")

# Model provider configs
models_cfg = _raw.get("models", {})
PROVIDERS = models_cfg.get("providers", {})
ROUTING = models_cfg.get("routing", {})

DEFAULT_PROVIDER = ROUTING.get("default", "nim")
FALLBACK_CHAIN = ROUTING.get("fallback_chain", ["nim", "ollama"])
LONG_CONTEXT_PROVIDER = ROUTING.get("long_context", "gemini")
ESCALATION_PROVIDER = ROUTING.get("escalation", "opus")
BRAIN_PROVIDER = ROUTING.get("brain", "haiku")
OPUS_DAILY_BUDGET = ROUTING.get("opus_daily_budget_tokens", 50000)

# Classifier
classifier_cfg = _raw.get("classifier", {})
COMPLEXITY_THRESHOLD = classifier_cfg.get("complexity_threshold", 60)
LONG_CONTEXT_CHARS = classifier_cfg.get("long_context_chars", 3000)
HAIKU_BAND = classifier_cfg.get("haiku_band", [40, 70])

# Memory
memory_cfg = _raw.get("memory", {})
MAX_MEMORIES = memory_cfg.get("max_memories", 200)
SUMMARY_THRESHOLD = memory_cfg.get("summary_threshold", 30)
EXTRACTION_ENABLED = memory_cfg.get("extraction_enabled", True)

# Scheduler
scheduler_cfg = _raw.get("scheduler", {})
TIMEZONE = scheduler_cfg.get("timezone", "America/New_York")
ACTIVE_HOURS = scheduler_cfg.get("active_hours", [7, 22])
HEARTBEAT_INTERVAL = scheduler_cfg.get("heartbeat_interval_minutes", 15)
IDLE_CHECKIN_MINUTES = scheduler_cfg.get("idle_checkin_minutes", 120)
REMINDER_CHECK_MINUTES = scheduler_cfg.get("reminder_check_minutes", 5)

# Tools
tools_cfg = _raw.get("tools", {})
TOOLS_ENABLED = tools_cfg.get("enabled", True)
MAX_AGENT_TURNS = tools_cfg.get("max_agent_turns", 10)
COMMAND_TIMEOUT = tools_cfg.get("command_timeout_seconds", 30)
ALLOWED_DIRECTORIES = tools_cfg.get("allowed_directories", [])
AUTO_APPROVE_READS = tools_cfg.get("auto_approve_reads", True)
AUTO_APPROVE_ALL = tools_cfg.get("auto_approve_all", False)

# ntfy
ntfy_cfg = _raw.get("ntfy", {})
NTFY_SERVER = os.getenv(ntfy_cfg.get("server_env", "NTFY_SERVER"), "")
NTFY_TOPIC = os.getenv(ntfy_cfg.get("topic_env", "NTFY_TOPIC"), "")
NTFY_TOKEN = os.getenv(ntfy_cfg.get("token_env", "NTFY_TOKEN"), "")
NTFY_ENABLED = ntfy_cfg.get("enabled", True)

# Telegram
telegram_cfg = _raw.get("telegram", {})
TELEGRAM_ENABLED = telegram_cfg.get("enabled", False)
TELEGRAM_BOT_TOKEN = os.getenv(telegram_cfg.get("token_env", "TELEGRAM_BOT_TOKEN"), "")
TELEGRAM_WEBHOOK_SECRET = os.getenv(telegram_cfg.get("webhook_secret_env", "TELEGRAM_WEBHOOK_SECRET"), "")
TELEGRAM_CHAT_ID = str(telegram_cfg.get("chat_id", ""))
TELEGRAM_WEBHOOK_URL = telegram_cfg.get("webhook_url", "")

# Legacy compat
SYSTEM_PROMPT = SYSTEM_PROMPT_TEMPLATE


def get_api_key(provider_name: str) -> str:
    """Resolve API key for a provider — direct value or env var lookup."""
    prov = PROVIDERS.get(provider_name, {})
    if "api_key" in prov:
        return prov["api_key"]
    env_var = prov.get("api_key_env", "")
    return os.getenv(env_var, "")


def get_raw() -> dict:
    """Return the raw parsed YAML config (for settings API)."""
    return _raw.copy()


def reload():
    """Re-read config.yaml and update module-level attributes."""
    global _raw, HOST, PORT, PERSONALITY_NAME, SYSTEM_PROMPT_TEMPLATE, SYSTEM_PROMPT
    global PROVIDERS, ROUTING, DEFAULT_PROVIDER, FALLBACK_CHAIN, LONG_CONTEXT_PROVIDER
    global ESCALATION_PROVIDER, BRAIN_PROVIDER, OPUS_DAILY_BUDGET
    global COMPLEXITY_THRESHOLD, LONG_CONTEXT_CHARS, HAIKU_BAND
    global MAX_MEMORIES, SUMMARY_THRESHOLD, EXTRACTION_ENABLED
    global TIMEZONE, ACTIVE_HOURS, HEARTBEAT_INTERVAL, IDLE_CHECKIN_MINUTES, REMINDER_CHECK_MINUTES
    global TOOLS_ENABLED, MAX_AGENT_TURNS, COMMAND_TIMEOUT, ALLOWED_DIRECTORIES, AUTO_APPROVE_READS, AUTO_APPROVE_ALL
    global NTFY_SERVER, NTFY_TOPIC, NTFY_TOKEN, NTFY_ENABLED
    global TELEGRAM_ENABLED, TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET, TELEGRAM_CHAT_ID, TELEGRAM_WEBHOOK_URL

    load_dotenv(SERVER_DIR / ".env", override=True)

    with open(_config_path) as f:
        _raw = yaml.safe_load(f)

    srv = _raw.get("server", {})
    HOST = srv.get("host", "0.0.0.0")
    PORT = srv.get("port", 8080)

    p = _raw.get("personality", {})
    PERSONALITY_NAME = p.get("name", "Conduit")
    SYSTEM_PROMPT_TEMPLATE = p.get("system_prompt", "You are a helpful AI assistant.")
    SYSTEM_PROMPT = SYSTEM_PROMPT_TEMPLATE

    m = _raw.get("models", {})
    PROVIDERS = m.get("providers", {})
    ROUTING = m.get("routing", {})
    DEFAULT_PROVIDER = ROUTING.get("default", "nim")
    FALLBACK_CHAIN = ROUTING.get("fallback_chain", ["nim", "ollama"])
    LONG_CONTEXT_PROVIDER = ROUTING.get("long_context", "gemini")
    ESCALATION_PROVIDER = ROUTING.get("escalation", "opus")
    BRAIN_PROVIDER = ROUTING.get("brain", "haiku")
    OPUS_DAILY_BUDGET = ROUTING.get("opus_daily_budget_tokens", 50000)

    c = _raw.get("classifier", {})
    COMPLEXITY_THRESHOLD = c.get("complexity_threshold", 60)
    LONG_CONTEXT_CHARS = c.get("long_context_chars", 3000)
    HAIKU_BAND = c.get("haiku_band", [40, 70])

    mem = _raw.get("memory", {})
    MAX_MEMORIES = mem.get("max_memories", 200)
    SUMMARY_THRESHOLD = mem.get("summary_threshold", 30)
    EXTRACTION_ENABLED = mem.get("extraction_enabled", True)

    s = _raw.get("scheduler", {})
    TIMEZONE = s.get("timezone", "America/New_York")
    ACTIVE_HOURS = s.get("active_hours", [7, 22])
    HEARTBEAT_INTERVAL = s.get("heartbeat_interval_minutes", 15)
    IDLE_CHECKIN_MINUTES = s.get("idle_checkin_minutes", 120)
    REMINDER_CHECK_MINUTES = s.get("reminder_check_minutes", 5)

    t = _raw.get("tools", {})
    TOOLS_ENABLED = t.get("enabled", True)
    MAX_AGENT_TURNS = t.get("max_agent_turns", 10)
    COMMAND_TIMEOUT = t.get("command_timeout_seconds", 30)
    ALLOWED_DIRECTORIES = t.get("allowed_directories", [])
    AUTO_APPROVE_READS = t.get("auto_approve_reads", True)
    AUTO_APPROVE_ALL = t.get("auto_approve_all", False)

    n = _raw.get("ntfy", {})
    NTFY_SERVER = os.getenv(n.get("server_env", "NTFY_SERVER"), "")
    NTFY_TOPIC = os.getenv(n.get("topic_env", "NTFY_TOPIC"), "")
    NTFY_TOKEN = os.getenv(n.get("token_env", "NTFY_TOKEN"), "")
    NTFY_ENABLED = n.get("enabled", True)

    tg = _raw.get("telegram", {})
    TELEGRAM_ENABLED = tg.get("enabled", False)
    TELEGRAM_BOT_TOKEN = os.getenv(tg.get("token_env", "TELEGRAM_BOT_TOKEN"), "")
    TELEGRAM_WEBHOOK_SECRET = os.getenv(tg.get("webhook_secret_env", "TELEGRAM_WEBHOOK_SECRET"), "")
    TELEGRAM_CHAT_ID = str(tg.get("chat_id", ""))
    TELEGRAM_WEBHOOK_URL = tg.get("webhook_url", "")
