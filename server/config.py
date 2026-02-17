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
HOST = server.get("host", "127.0.0.1")
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
EMBEDDING_MODEL = memory_cfg.get("embedding_model", "text-embedding-005")
EMBEDDING_DIMENSIONS = memory_cfg.get("embedding_dimensions", 768)
SEARCH_TOP_K = memory_cfg.get("search_top_k", 15)
IMPORTANCE_FLOOR = memory_cfg.get("importance_floor", 8)
DEDUP_THRESHOLD = memory_cfg.get("dedup_threshold", 0.9)
BM25_ENABLED = memory_cfg.get("bm25_enabled", False)
BM25_DB_PATH = memory_cfg.get("bm25_db_path", "~/conduit-data/memory_index.db")
HYBRID_TOP_K = memory_cfg.get("hybrid_top_k", 10)

# Indexer
indexer_cfg = _raw.get("indexer", {})
INDEXER_ENABLED = indexer_cfg.get("enabled", False)
INDEXER_OUTPUT_DIR = indexer_cfg.get("output_dir", "~/conduit-data/indexes")
INDEXER_PROJECTS = indexer_cfg.get("projects", [])

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

# Agents
agents_cfg = _raw.get("agents", {})
AGENTS_LIST = agents_cfg.get("list", [])
AGENTS_COMMS = agents_cfg.get("communication", {})
BINDINGS_LIST = _raw.get("bindings", [])

# Skills
skills_cfg = _raw.get("skills", {})
SKILL_GROCERY_ENABLED = skills_cfg.get("grocery", {}).get("enabled", True)
SKILL_EXPENSES_ENABLED = skills_cfg.get("expenses", {}).get("enabled", True)
SKILL_CALENDAR_ENABLED = skills_cfg.get("calendar", {}).get("enabled", True)

# Markdown Skills (OpenClaw compatible)
md_skills_cfg = _raw.get("markdown_skills", {})
MARKDOWN_SKILLS_ENABLED = md_skills_cfg.get("enabled", True)
MARKDOWN_SKILLS_DIR = md_skills_cfg.get("dir", "~/.conduit/skills")
MARKDOWN_SKILLS_MAX_PER_TURN = md_skills_cfg.get("max_per_turn", 2)

# Plugins
plugins_cfg = _raw.get("plugins", {})
PLUGINS_ENABLED = plugins_cfg.get("enabled", True)
PLUGINS_DIR = plugins_cfg.get("dir", "~/.conduit/plugins")

# Subagents
subagents_cfg = agents_cfg.get("subagents", {})
SUBAGENTS_ENABLED = subagents_cfg.get("enabled", True)
SUBAGENTS_MAX_SPAWN_DEPTH = subagents_cfg.get("max_spawn_depth", 2)
SUBAGENTS_MAX_CHILDREN = subagents_cfg.get("max_children", 5)
SUBAGENTS_DEFAULT_TIMEOUT = subagents_cfg.get("default_timeout", 300)
SUBAGENTS_SESSION_TTL_MINUTES = subagents_cfg.get("session_ttl_minutes", 60)

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

# Watcher
watcher_cfg = _raw.get("watcher", {})
WATCHER_ENABLED = watcher_cfg.get("enabled", False)
WATCHER_DIRECTORIES = watcher_cfg.get("directories", [])
SPECTRE_API = watcher_cfg.get("spectre_api", "http://localhost:8000")
WATCHER_SORT_BASE = watcher_cfg.get("sort_base", "~/Documents/Sorted")
WATCHER_DEBOUNCE = watcher_cfg.get("debounce_seconds", 3)

# Thresholds
thresholds_cfg = _raw.get("thresholds", {})
FOOD_COST_WARNING = thresholds_cfg.get("food_cost_warning", 0.50)
FOOD_COST_TARGET = thresholds_cfg.get("food_cost_target", 0.45)
HEALTH_SCORE_MINIMUM = thresholds_cfg.get("health_score_minimum", 60)
ALERT_COOLDOWN_MINUTES = thresholds_cfg.get("alert_cooldown_minutes", 360)

# Web / SearXNG
web_cfg = _raw.get("web", {})
SEARXNG_URL = web_cfg.get("searxng_url", "http://localhost:8888")
WEB_FETCH_TIMEOUT = web_cfg.get("fetch_timeout_seconds", 15)
WEB_SEARCH_ENABLED = web_cfg.get("enabled", True)
DEEP_SEARCH_CACHE_TTL = web_cfg.get("deep_search_cache_ttl_seconds", 900)
DEEP_SEARCH_MAX_PAGES = web_cfg.get("deep_search_max_pages", 3)
DEEP_SEARCH_MAX_CHUNKS = web_cfg.get("deep_search_max_chunks", 10)

# Voice (OpenAI Whisper + TTS)
voice_cfg = _raw.get("voice", {})
VOICE_ENABLED = voice_cfg.get("enabled", False)
OPENAI_API_KEY = os.getenv(voice_cfg.get("openai_api_key_env", "OPENAI_API_KEY"), "")
VOICE_STT_MODEL = voice_cfg.get("stt_model", "whisper-1")
VOICE_TTS_MODEL = voice_cfg.get("tts_model", "tts-1")
VOICE_TTS_VOICE = voice_cfg.get("tts_voice", "alloy")

# Outlook
outlook_cfg = _raw.get("outlook", {})
OUTLOOK_CLIENT_ID = os.getenv(outlook_cfg.get("client_id_env", "OUTLOOK_CLIENT_ID"), "")
OUTLOOK_ENABLED = outlook_cfg.get("enabled", True)
OUTLOOK_POLL_INTERVAL = outlook_cfg.get("poll_interval_minutes", 15)

# Worker
worker_cfg = _raw.get("worker", {})
WORKER_ENABLED = worker_cfg.get("enabled", False)
WORKER_REDDIT_USERNAME = worker_cfg.get("reddit_username", "")
WORKER_CYCLE_CRON = worker_cfg.get("cycle_cron", "0 10,20 * * *")
WORKER_DIGEST_CRON = worker_cfg.get("digest_cron", "0 5 * * *")
WORKER_DATA_DIR = worker_cfg.get("data_dir", "~/conduit/server/data/worker")
WORKER_IDEATION_PROVIDER = worker_cfg.get("ideation_provider", "minimax")
WORKER_PLANNING_PROVIDER = worker_cfg.get("planning_provider", "haiku")
WORKER_BUILDING_PROVIDER = worker_cfg.get("building_provider", "claude_code")
WORKER_PROPOSAL_TIMEOUT_HOURS = worker_cfg.get("proposal_timeout_hours", 48)

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
    global EMBEDDING_MODEL, EMBEDDING_DIMENSIONS, SEARCH_TOP_K, IMPORTANCE_FLOOR, DEDUP_THRESHOLD
    global BM25_ENABLED, BM25_DB_PATH, HYBRID_TOP_K
    global INDEXER_ENABLED, INDEXER_OUTPUT_DIR, INDEXER_PROJECTS
    global TIMEZONE, ACTIVE_HOURS, HEARTBEAT_INTERVAL, IDLE_CHECKIN_MINUTES, REMINDER_CHECK_MINUTES
    global TOOLS_ENABLED, MAX_AGENT_TURNS, COMMAND_TIMEOUT, ALLOWED_DIRECTORIES, AUTO_APPROVE_READS, AUTO_APPROVE_ALL
    global AGENTS_LIST, AGENTS_COMMS, BINDINGS_LIST
    global SKILL_GROCERY_ENABLED, SKILL_EXPENSES_ENABLED, SKILL_CALENDAR_ENABLED
    global MARKDOWN_SKILLS_ENABLED, MARKDOWN_SKILLS_DIR, MARKDOWN_SKILLS_MAX_PER_TURN
    global PLUGINS_ENABLED, PLUGINS_DIR
    global SUBAGENTS_ENABLED, SUBAGENTS_MAX_SPAWN_DEPTH, SUBAGENTS_MAX_CHILDREN
    global SUBAGENTS_DEFAULT_TIMEOUT, SUBAGENTS_SESSION_TTL_MINUTES
    global NTFY_SERVER, NTFY_TOPIC, NTFY_TOKEN, NTFY_ENABLED
    global TELEGRAM_ENABLED, TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET, TELEGRAM_CHAT_ID, TELEGRAM_WEBHOOK_URL
    global WATCHER_ENABLED, WATCHER_DIRECTORIES, SPECTRE_API, WATCHER_SORT_BASE, WATCHER_DEBOUNCE
    global FOOD_COST_WARNING, FOOD_COST_TARGET, HEALTH_SCORE_MINIMUM, ALERT_COOLDOWN_MINUTES
    global SEARXNG_URL, WEB_FETCH_TIMEOUT, WEB_SEARCH_ENABLED
    global DEEP_SEARCH_CACHE_TTL, DEEP_SEARCH_MAX_PAGES, DEEP_SEARCH_MAX_CHUNKS
    global VOICE_ENABLED, OPENAI_API_KEY, VOICE_STT_MODEL, VOICE_TTS_MODEL, VOICE_TTS_VOICE
    global OUTLOOK_CLIENT_ID, OUTLOOK_ENABLED, OUTLOOK_POLL_INTERVAL
    global WORKER_ENABLED, WORKER_REDDIT_USERNAME, WORKER_CYCLE_CRON, WORKER_DIGEST_CRON
    global WORKER_DATA_DIR, WORKER_IDEATION_PROVIDER, WORKER_PLANNING_PROVIDER
    global WORKER_BUILDING_PROVIDER, WORKER_PROPOSAL_TIMEOUT_HOURS

    load_dotenv(SERVER_DIR / ".env", override=True)

    with open(_config_path) as f:
        _raw = yaml.safe_load(f)

    srv = _raw.get("server", {})
    HOST = srv.get("host", "127.0.0.1")
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
    EMBEDDING_MODEL = mem.get("embedding_model", "text-embedding-005")
    EMBEDDING_DIMENSIONS = mem.get("embedding_dimensions", 768)
    SEARCH_TOP_K = mem.get("search_top_k", 15)
    IMPORTANCE_FLOOR = mem.get("importance_floor", 8)
    DEDUP_THRESHOLD = mem.get("dedup_threshold", 0.9)
    BM25_ENABLED = mem.get("bm25_enabled", False)
    BM25_DB_PATH = mem.get("bm25_db_path", "~/conduit-data/memory_index.db")
    HYBRID_TOP_K = mem.get("hybrid_top_k", 10)

    ix = _raw.get("indexer", {})
    INDEXER_ENABLED = ix.get("enabled", False)
    INDEXER_OUTPUT_DIR = ix.get("output_dir", "~/conduit-data/indexes")
    INDEXER_PROJECTS = ix.get("projects", [])

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

    ag = _raw.get("agents", {})
    AGENTS_LIST = ag.get("list", [])
    AGENTS_COMMS = ag.get("communication", {})
    BINDINGS_LIST = _raw.get("bindings", [])

    sk = _raw.get("skills", {})
    SKILL_GROCERY_ENABLED = sk.get("grocery", {}).get("enabled", True)
    SKILL_EXPENSES_ENABLED = sk.get("expenses", {}).get("enabled", True)
    SKILL_CALENDAR_ENABLED = sk.get("calendar", {}).get("enabled", True)

    mds = _raw.get("markdown_skills", {})
    MARKDOWN_SKILLS_ENABLED = mds.get("enabled", True)
    MARKDOWN_SKILLS_DIR = mds.get("dir", "~/.conduit/skills")
    MARKDOWN_SKILLS_MAX_PER_TURN = mds.get("max_per_turn", 2)

    plg = _raw.get("plugins", {})
    PLUGINS_ENABLED = plg.get("enabled", True)
    PLUGINS_DIR = plg.get("dir", "~/.conduit/plugins")

    sub = ag.get("subagents", {})
    SUBAGENTS_ENABLED = sub.get("enabled", True)
    SUBAGENTS_MAX_SPAWN_DEPTH = sub.get("max_spawn_depth", 2)
    SUBAGENTS_MAX_CHILDREN = sub.get("max_children", 5)
    SUBAGENTS_DEFAULT_TIMEOUT = sub.get("default_timeout", 300)
    SUBAGENTS_SESSION_TTL_MINUTES = sub.get("session_ttl_minutes", 60)

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

    w = _raw.get("watcher", {})
    WATCHER_ENABLED = w.get("enabled", False)
    WATCHER_DIRECTORIES = w.get("directories", [])
    SPECTRE_API = w.get("spectre_api", "http://localhost:8000")
    WATCHER_SORT_BASE = w.get("sort_base", "~/Documents/Sorted")
    WATCHER_DEBOUNCE = w.get("debounce_seconds", 3)

    th = _raw.get("thresholds", {})
    FOOD_COST_WARNING = th.get("food_cost_warning", 0.50)
    FOOD_COST_TARGET = th.get("food_cost_target", 0.45)
    HEALTH_SCORE_MINIMUM = th.get("health_score_minimum", 60)
    ALERT_COOLDOWN_MINUTES = th.get("alert_cooldown_minutes", 360)

    wb = _raw.get("web", {})
    SEARXNG_URL = wb.get("searxng_url", "http://localhost:8888")
    WEB_FETCH_TIMEOUT = wb.get("fetch_timeout_seconds", 15)
    WEB_SEARCH_ENABLED = wb.get("enabled", True)
    DEEP_SEARCH_CACHE_TTL = wb.get("deep_search_cache_ttl_seconds", 900)
    DEEP_SEARCH_MAX_PAGES = wb.get("deep_search_max_pages", 3)
    DEEP_SEARCH_MAX_CHUNKS = wb.get("deep_search_max_chunks", 10)

    vc = _raw.get("voice", {})
    VOICE_ENABLED = vc.get("enabled", False)
    OPENAI_API_KEY = os.getenv(vc.get("openai_api_key_env", "OPENAI_API_KEY"), "")
    VOICE_STT_MODEL = vc.get("stt_model", "whisper-1")
    VOICE_TTS_MODEL = vc.get("tts_model", "tts-1")
    VOICE_TTS_VOICE = vc.get("tts_voice", "alloy")

    ol = _raw.get("outlook", {})
    OUTLOOK_CLIENT_ID = os.getenv(ol.get("client_id_env", "OUTLOOK_CLIENT_ID"), "")
    OUTLOOK_ENABLED = ol.get("enabled", True)
    OUTLOOK_POLL_INTERVAL = ol.get("poll_interval_minutes", 15)

    wk = _raw.get("worker", {})
    WORKER_ENABLED = wk.get("enabled", False)
    WORKER_REDDIT_USERNAME = wk.get("reddit_username", "")
    WORKER_CYCLE_CRON = wk.get("cycle_cron", "0 10,20 * * *")
    WORKER_DIGEST_CRON = wk.get("digest_cron", "0 5 * * *")
    WORKER_DATA_DIR = wk.get("data_dir", "~/conduit/server/data/worker")
    WORKER_IDEATION_PROVIDER = wk.get("ideation_provider", "minimax")
    WORKER_PLANNING_PROVIDER = wk.get("planning_provider", "haiku")
    WORKER_BUILDING_PROVIDER = wk.get("building_provider", "claude_code")
    WORKER_PROPOSAL_TIMEOUT_HOURS = wk.get("proposal_timeout_hours", 48)
