"""FastAPI app — WebSocket endpoint, message handler, fallback chain, settings API."""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from . import config, db
from .models.base import StreamChunk, StreamDone
from .ws import ConnectionManager
from .tools import get_all as get_all_tools

log = logging.getLogger("conduit")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

manager = ConnectionManager()

# Provider registry — populated at startup
providers: dict = {}

# Lazy imports to avoid circular deps
router_module = None
scheduler_module = None
memory_module = None

# Telegram bot instance (initialized at startup if configured)
telegram_bot = None


def _build_providers():
    """Instantiate model providers from config."""
    from .models.openai_compat import OpenAICompatProvider
    from .models.gemini import GeminiProvider
    from .models.anthropic import AnthropicProvider
    from .models.claude_code import ClaudeCodeProvider

    providers.clear()

    for name, prov_cfg in config.PROVIDERS.items():
        ptype = prov_cfg.get("type")
        api_key = config.get_api_key(name)

        if ptype == "openai_compat" and api_key:
            providers[name] = OpenAICompatProvider(
                name=name,
                base_url=prov_cfg["base_url"],
                api_key=api_key,
                model=prov_cfg.get("default_model", ""),
            )
        elif ptype == "gemini":
            use_vertex = prov_cfg.get("vertex", False)
            project_env = prov_cfg.get("project_env", "")
            gcp_project = os.getenv(project_env, "") if project_env else ""
            if use_vertex and gcp_project:
                providers[name] = GeminiProvider(
                    name=name,
                    model=prov_cfg.get("default_model", ""),
                    vertex=True,
                    project=gcp_project,
                    location=prov_cfg.get("location", "us-east4"),
                )
            elif not use_vertex and api_key:
                providers[name] = GeminiProvider(
                    name=name,
                    api_key=api_key,
                    model=prov_cfg.get("default_model", ""),
                )
        elif ptype == "anthropic" and api_key:
            providers[name] = AnthropicProvider(
                name=name,
                api_key=api_key,
                model=prov_cfg.get("model", "claude-opus-4-6"),
            )
        elif ptype == "claude_code":
            providers[name] = ClaudeCodeProvider(
                name=name,
                model=prov_cfg.get("model", "sonnet"),
                working_dir=os.path.expanduser(prov_cfg.get("working_dir", "~")),
                max_budget_usd=prov_cfg.get("max_budget_usd", 0),
                timeout=prov_cfg.get("timeout", 600),
            )


def render_system_prompt() -> str:
    """Build the system prompt with injected context."""
    now = datetime.now()
    template = config.SYSTEM_PROMPT_TEMPLATE

    # Get memory context if available
    memory_context = ""
    # Memory will be injected asynchronously — use sync placeholder for now
    # The async version is called in handle_message

    # Get pending reminders
    pending = ""

    prompt = template.format(
        name=config.PERSONALITY_NAME,
        time=now.strftime("%I:%M %p"),
        date=now.strftime("%B %d, %Y"),
        day=now.strftime("%A"),
        memories=memory_context,
        pending_tasks=pending,
        tools_context="",
    )
    return prompt


async def render_system_prompt_async() -> str:
    """Build the system prompt with async context (memories, tasks)."""
    now = datetime.now()
    template = config.SYSTEM_PROMPT_TEMPLATE

    # Get memory context
    memory_context = ""
    if memory_module:
        try:
            memory_context = await memory_module.get_memory_context()
        except Exception as e:
            log.warning("Failed to get memory context: %s", e)

    # Get pending reminders
    pending = ""
    try:
        raw = await db.kv_get("reminders")
        if raw:
            reminders = json.loads(raw)
            active = [r for r in reminders if r["due"] > now.timestamp()]
            if active:
                lines = ["Pending reminders:"]
                for r in active:
                    due_str = datetime.fromtimestamp(r["due"]).strftime("%I:%M %p")
                    lines.append(f"- {r['text']} (due {due_str})")
                pending = "\n".join(lines)
    except Exception:
        pass

    # Build tools context
    tools_context = ""
    if config.TOOLS_ENABLED and config.ALLOWED_DIRECTORIES:
        lines = ["You have access to tools that can read and search files. Available directories:"]
        dir_descriptions = {
            "~/Documents/Work/lockheed/": "sales PDFs, inventory files",
            "~/Projects/spectre/": "operations dashboard code",
            "~/Projects/conduit/": "this project's source code",
            "~/Documents/Sorted/": "auto-sorted downloads",
        }
        for d in config.ALLOWED_DIRECTORIES:
            desc = dir_descriptions.get(d, "")
            line = f"  - {d}"
            if desc:
                line += f" ({desc})"
            lines.append(line)
        lines.append("Use tools when the user asks about files, directories, code, or needs file operations.")
        tools_context = "\n".join(lines)

    prompt = template.format(
        name=config.PERSONALITY_NAME,
        time=now.strftime("%I:%M %p"),
        date=now.strftime("%B %d, %Y"),
        day=now.strftime("%A"),
        memories=memory_context,
        pending_tasks=pending,
        tools_context=tools_context,
    )
    return prompt


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown."""
    await db.init_db()
    _build_providers()
    log.info("Providers loaded: %s", list(providers.keys()))

    # Restore saved OpenRouter model override
    or_prov = providers.get("openrouter")
    if or_prov:
        saved_model = await db.kv_get("openrouter_model")
        if saved_model:
            or_prov.model = saved_model
            log.info("OpenRouter model restored: %s", saved_model)

    # Register tools
    if config.TOOLS_ENABLED:
        from .tools.filesystem import register_all as register_fs_tools
        register_fs_tools()
        try:
            from .tools.write import register_all as register_write_tools
            register_write_tools()
        except ImportError:
            pass
        try:
            from .tools.execute import register_all as register_exec_tools
            register_exec_tools()
        except ImportError:
            pass
        log.info("Tools registered: %s", [t.name for t in get_all_tools()])

    # Load memory module
    global memory_module
    try:
        from . import memory as mem_mod
        memory_module = mem_mod
        log.info("Memory system loaded")
    except Exception as e:
        log.warning("Memory system not available: %s", e)

    # Start scheduler if available
    global scheduler_module
    try:
        from . import scheduler as sched_mod
        scheduler_module = sched_mod
        await sched_mod.start(manager)
        log.info("Scheduler started")
    except Exception as e:
        log.warning("Scheduler not available: %s", e)

    # Initialize Telegram bot if configured
    global telegram_bot
    if config.TELEGRAM_ENABLED and config.TELEGRAM_BOT_TOKEN:
        from .telegram import TelegramBot
        telegram_bot = TelegramBot(config.TELEGRAM_BOT_TOKEN)
        if config.TELEGRAM_WEBHOOK_URL:
            await telegram_bot.set_webhook(config.TELEGRAM_WEBHOOK_URL, config.TELEGRAM_WEBHOOK_SECRET)
        log.info("Telegram bot initialized")

    yield

    if telegram_bot:
        await telegram_bot.delete_webhook()
    if scheduler_module:
        await scheduler_module.stop()


app = FastAPI(title="Conduit", lifespan=lifespan)


def get_provider(name: str | None = None):
    """Get a provider by name, falling back to default."""
    name = name or config.DEFAULT_PROVIDER
    prov = providers.get(name)
    if not prov:
        available = list(providers.keys())
        if available:
            return providers[available[0]]
        raise RuntimeError("No model providers configured")
    return prov


async def stream_with_fallback(messages: list[dict], system: str,
                                provider_name: str | None = None,
                                ws: WebSocket | None = None) -> tuple[str, "Usage | None", "BaseProvider"]:
    """Stream a response, walking the fallback chain on errors.

    Returns (response_text, usage, provider_that_succeeded).
    """
    from .models.base import Usage

    # Build ordered provider list: requested first, then fallback chain
    chain = []
    if provider_name and provider_name in providers:
        chain.append(provider_name)
    for name in config.FALLBACK_CHAIN:
        if name not in chain and name in providers:
            chain.append(name)
    # Add any remaining providers as last resort
    for name in providers:
        if name not in chain:
            chain.append(name)

    if not chain:
        raise RuntimeError("No model providers configured")

    last_error = None
    for pname in chain:
        provider = providers[pname]
        try:
            full_response = []
            usage = None

            async for item in provider.stream(messages, system=system):
                if isinstance(item, StreamChunk):
                    if ws:
                        await manager.send_chunk(ws, item.text)
                    full_response.append(item.text)
                elif isinstance(item, StreamDone):
                    usage = item.usage

            return "".join(full_response), usage, provider

        except Exception as e:
            last_error = e
            log.warning("Provider %s failed: %s — trying next in chain", pname, e)
            continue

    # All providers failed
    raise RuntimeError(f"All providers failed. Last error: {last_error}")


async def handle_message(ws: WebSocket, data: dict, conversation_id: str):
    """Process an incoming chat message — classify, route, stream back."""
    content = data.get("content", "").strip()
    if not content:
        return

    # Track user activity
    await db.kv_set("last_user_activity", str(datetime.now().timestamp()))

    # Store user message
    await db.add_message(conversation_id, "user", content)

    # Check for commands (Tier 0)
    if content.startswith("/"):
        handled = await handle_command(ws, content, conversation_id)
        if handled:
            return

    # Classify and route
    global router_module
    if router_module is None:
        try:
            from . import router as rm
            router_module = rm
        except Exception:
            pass

    # Build message history (need count for classifier)
    history = await db.get_messages(conversation_id, limit=50)
    conversation_length = len(history)

    provider_name = None
    intent = None
    if router_module:
        provider_name = await router_module.route(content, providers, conversation_length)
        # Get intent for reminder detection
        from .classifier import classify_fast
        intent, _ = classify_fast(content, conversation_length)

    # Handle natural language reminders — respond AND extract
    if intent and intent.name == "REMINDER":
        from .scheduler import parse_remind
        reminder_result = await parse_remind(content)
        # Don't short-circuit — let the model respond naturally too
        # The reminder is extracted in the background

    # Strip /command prefix before sending to model
    model_content = content
    if router_module:
        model_content = router_module.strip_command(content)
    messages = []
    for msg in history[:-1]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": model_content})

    # Get context-aware system prompt
    system = await render_system_prompt_async()

    # Stream with fallback chain
    await manager.send_typing(ws)

    try:
        selected_provider = get_provider(provider_name)
        tools = get_all_tools()

        if getattr(selected_provider, 'manages_own_tools', False):
            # Claude Code: spawns its own subprocess, manages its own tools
            session_key = f"cc_session:{conversation_id}"
            session_id = await db.kv_get(session_key)
            response_text, usage, new_session_id, cost_usd = await selected_provider.run(
                prompt=model_content, session_id=session_id, ws=ws, manager=manager,
            )
            if new_session_id and new_session_id != session_id:
                await db.kv_set(session_key, new_session_id)
            provider = selected_provider
        elif selected_provider.supports_tools and config.TOOLS_ENABLED and tools:
            from . import agent
            response_text, usage = await agent.run_agent_loop(
                messages, system, selected_provider, tools, ws, manager,
                max_turns=config.MAX_AGENT_TURNS,
            )
            provider = selected_provider
        else:
            response_text, usage, provider = await stream_with_fallback(
                messages, system, provider_name=provider_name, ws=ws
            )
    except Exception as e:
        log.error("All providers failed: %s", e)
        await manager.send_error(ws, f"All providers failed: {e}")
        return

    await manager.send_done(ws)

    # Send meta info
    if usage:
        await manager.send_meta(ws, provider.model, usage.input_tokens, usage.output_tokens)
        await db.log_usage(provider.name, provider.model, usage.input_tokens, usage.output_tokens)

    # Store assistant message
    if response_text:
        await db.add_message(conversation_id, "assistant", response_text,
                             model=provider.model, source=provider.name)

    # Background: extract memories
    if memory_module and config.EXTRACTION_ENABLED:
        asyncio.create_task(
            memory_module.extract_memories(content, response_text, conversation_id)
        )

    # Background: check if summarization needed
    if memory_module:
        msg_count = len(history)
        if msg_count > 0 and msg_count % config.SUMMARY_THRESHOLD == 0:
            asyncio.create_task(
                memory_module.summarize_conversation(conversation_id)
            )


async def handle_command(ws: WebSocket, content: str, conversation_id: str) -> bool:
    """Handle /commands. Returns True if handled."""
    parts = content.split(maxsplit=1)
    cmd = parts[0].lower()

    if cmd == "/clear":
        new_id = await db.create_conversation()
        await manager.send_chunk(ws, "Conversation cleared.")
        await manager.send_done(ws)
        return True

    if cmd == "/models":
        lines = ["**Available providers:**"]
        for name, prov in providers.items():
            role = config.PROVIDERS.get(name, {}).get("role", "")
            lines.append(f"- **{name}**: {prov.model} ({role})")
        await manager.send_chunk(ws, "\n".join(lines))
        await manager.send_done(ws)
        return True

    if cmd == "/usage":
        tokens = await db.get_daily_opus_tokens()
        budget = config.OPUS_DAILY_BUDGET
        # Also show haiku usage
        haiku_tokens = await db.get_daily_provider_tokens("haiku")
        cc_tokens = await db.get_daily_provider_tokens("claude_code")
        lines = [
            f"**Opus**: {tokens:,} / {budget:,} output tokens today",
            f"**Haiku**: {haiku_tokens:,} output tokens today",
            f"**Claude Code**: {cc_tokens:,} output tokens today",
        ]
        await manager.send_chunk(ws, "\n".join(lines))
        await manager.send_done(ws)
        return True

    if cmd == "/remind":
        from .scheduler import parse_remind
        result = await parse_remind(content)
        await manager.send_chunk(ws, result)
        await manager.send_done(ws)
        return True

    if cmd == "/schedule":
        tasks = await db.get_scheduled_tasks()
        if tasks:
            lines = ["**Scheduled tasks:**"]
            for t in tasks:
                status = "enabled" if t["enabled"] else "disabled"
                lines.append(f"- **{t['name']}** -- `{t['cron']}` ({status})")
            await manager.send_chunk(ws, "\n".join(lines))
        else:
            await manager.send_chunk(ws, "No scheduled tasks.")
        await manager.send_done(ws)
        return True

    if cmd == "/memories":
        if memory_module:
            memories = await memory_module.get_all_memories()
            if memories:
                lines = [f"**Memories ({len(memories)}):**"]
                for m in memories[:20]:
                    lines.append(f"- [{m['category']}] {m['content']}")
                if len(memories) > 20:
                    lines.append(f"*...and {len(memories) - 20} more*")
                await manager.send_chunk(ws, "\n".join(lines))
            else:
                await manager.send_chunk(ws, "No memories stored yet.")
        else:
            await manager.send_chunk(ws, "Memory system not available.")
        await manager.send_done(ws)
        return True

    if cmd == "/permissions":
        override = await db.kv_get("auto_approve_tools")
        if override is not None:
            is_on = override == "true"
        else:
            is_on = config.AUTO_APPROVE_ALL
        new_val = not is_on
        await db.kv_set("auto_approve_tools", "true" if new_val else "false")
        status = "**ON** -- all tools auto-approved (no permission prompts)" if new_val else "**OFF** -- write/execute tools require approval"
        await manager.send_chunk(ws, f"Tool auto-approve: {status}")
        await manager.send_done(ws)
        return True

    if cmd == "/model":
        arg = parts[1].strip() if len(parts) > 1 else ""
        or_provider = providers.get("openrouter")
        if not or_provider:
            await manager.send_chunk(ws, "OpenRouter provider not configured.")
            await manager.send_done(ws)
            return True
        if not arg or arg == "list":
            current = or_provider.model
            saved = await db.kv_get("openrouter_model")
            default = config.PROVIDERS.get("openrouter", {}).get("default_model", "openrouter/free")
            lines = [
                f"**Active model:** `{current}`",
                f"**Config default:** `{default}`",
                "",
                "**Quick picks:**",
                "- `openrouter/free` -- auto-route free models",
                "- `google/gemini-2.5-flash` -- fast, free",
                "- `x-ai/grok-4.1-fast` -- free",
                "- `deepseek/deepseek-chat-v3.2` -- free",
                "- `openai/gpt-oss-120b` -- free",
                "",
                "Usage: `/model <model-id>` or `/model reset`",
            ]
            await manager.send_chunk(ws, "\n".join(lines))
        elif arg == "reset":
            default = config.PROVIDERS.get("openrouter", {}).get("default_model", "openrouter/free")
            or_provider.model = default
            await db.kv_set("openrouter_model", "")
            await manager.send_chunk(ws, f"OpenRouter model reset to `{default}`")
        else:
            or_provider.model = arg
            await db.kv_set("openrouter_model", arg)
            await manager.send_chunk(ws, f"OpenRouter model set to `{arg}`")
        await manager.send_done(ws)
        return True

    if cmd == "/help":
        await manager.send_chunk(ws, "\n".join([
            "**Commands:**",
            "- `/clear` -- new conversation",
            "- `/models` -- list providers",
            "- `/model <id>` -- switch OpenRouter model",
            "- `/usage` -- token budget status",
            "- `/memories` -- view stored memories",
            "- `/permissions` -- toggle tool auto-approve",
            "- `/remind <task> at <time>` -- set a reminder",
            "- `/remind <task> in <N> minutes/hours`",
            "- `/schedule` -- list scheduled tasks",
            "- `/or <query>` -- use OpenRouter",
            "- `/research <query>` -- use Gemini",
            "- `/opus <query>` -- use Opus (budget-capped)",
            "- `/think <query>` -- use Opus for deep thinking",
            "- `/code <query>` -- use Claude Code (CLI with tools)",
            "",
            "Or just talk naturally -- I'll figure out the rest.",
        ]))
        await manager.send_done(ws)
        return True

    return False


# --- REST endpoints for web UI ---

@app.get("/api/conversations")
async def api_conversations():
    return await db.list_conversations()


@app.get("/api/conversations/{cid}/messages")
async def api_messages(cid: str):
    return await db.get_messages(cid)


@app.post("/api/conversations")
async def api_new_conversation():
    cid = await db.create_conversation()
    return {"id": cid}


# --- Settings API ---

@app.get("/api/settings")
async def api_get_settings():
    from .settings import get_full_settings
    return get_full_settings()


@app.put("/api/settings/personality")
async def api_set_personality(body: dict):
    from .settings import get_config, save_config
    cfg = get_config()
    personality = cfg.setdefault("personality", {})
    if "name" in body:
        personality["name"] = body["name"]
    if "system_prompt" in body:
        personality["system_prompt"] = body["system_prompt"]
    save_config(cfg)
    return {"ok": True}


@app.put("/api/settings/providers/{name}")
async def api_set_provider(name: str, body: dict):
    from .settings import get_config, save_config, set_env_var
    cfg = get_config()
    providers_cfg = cfg.setdefault("models", {}).setdefault("providers", {})

    if name not in providers_cfg:
        providers_cfg[name] = {}

    prov = providers_cfg[name]
    for key in ("base_url", "model", "default_model", "type", "role", "vertex", "location"):
        if key in body:
            prov[key] = body[key]

    if "enabled" in body and not body["enabled"]:
        # Disable by removing
        providers_cfg.pop(name, None)
    else:
        providers_cfg[name] = prov

    # Handle API key — write to .env
    if "api_key" in body and body["api_key"]:
        env_var = prov.get("api_key_env", f"{name.upper()}_API_KEY")
        set_env_var(env_var, body["api_key"])

    save_config(cfg)

    # Rebuild providers
    _build_providers()
    return {"ok": True}


@app.put("/api/settings/routing")
async def api_set_routing(body: dict):
    from .settings import get_config, save_config
    cfg = get_config()
    routing = cfg.setdefault("models", {}).setdefault("routing", {})
    for key in ("default", "fallback_chain", "long_context", "escalation", "brain", "opus_daily_budget_tokens"):
        if key in body:
            routing[key] = body[key]
    save_config(cfg)
    return {"ok": True}


@app.put("/api/settings/scheduler")
async def api_set_scheduler(body: dict):
    from .settings import get_config, save_config
    cfg = get_config()
    sched = cfg.setdefault("scheduler", {})
    for key in ("active_hours", "heartbeat_interval_minutes", "idle_checkin_minutes", "reminder_check_minutes"):
        if key in body:
            sched[key] = body[key]
    save_config(cfg)
    return {"ok": True}


@app.put("/api/settings/memory")
async def api_set_memory(body: dict):
    from .settings import get_config, save_config
    cfg = get_config()
    mem = cfg.setdefault("memory", {})
    for key in ("max_memories", "summary_threshold", "extraction_enabled"):
        if key in body:
            mem[key] = body[key]
    save_config(cfg)
    return {"ok": True}


@app.put("/api/settings/tools")
async def api_set_tools(body: dict):
    from .settings import get_config, save_config
    cfg = get_config()
    tools_cfg = cfg.setdefault("tools", {})
    for key in ("enabled", "max_agent_turns", "command_timeout_seconds", "allowed_directories", "auto_approve_reads", "auto_approve_all"):
        if key in body:
            tools_cfg[key] = body[key]
    save_config(cfg)
    return {"ok": True}


@app.put("/api/settings/ntfy")
async def api_set_ntfy(body: dict):
    from .settings import get_config, save_config, set_env_var
    cfg = get_config()
    ntfy_cfg = cfg.setdefault("ntfy", {})
    if "enabled" in body:
        ntfy_cfg["enabled"] = body["enabled"]
    # Write env vars
    if "server" in body:
        set_env_var("NTFY_SERVER", body["server"])
    if "topic" in body:
        set_env_var("NTFY_TOPIC", body["topic"])
    if "token" in body:
        set_env_var("NTFY_TOKEN", body["token"])
    save_config(cfg)
    return {"ok": True}


@app.get("/api/settings/usage")
async def api_get_usage():
    daily = await db.get_usage_by_provider(days=1)
    weekly = await db.get_usage_by_provider(days=7)
    opus_today = await db.get_daily_opus_tokens()
    return {
        "daily": daily,
        "weekly": weekly,
        "opus_today": opus_today,
        "opus_budget": config.OPUS_DAILY_BUDGET,
    }


@app.get("/api/memories")
async def api_get_memories():
    if memory_module:
        return await memory_module.get_all_memories()
    return []


@app.delete("/api/memories/{memory_id}")
async def api_delete_memory(memory_id: str):
    await db.delete_memory(memory_id)
    return {"ok": True}


@app.post("/api/settings/test-ntfy")
async def api_test_ntfy():
    from . import ntfy as ntfy_mod
    await ntfy_mod.push(
        title="Test Notification",
        body="If you're seeing this, ntfy is working!",
        tags=["white_check_mark"],
        priority=3,
    )
    return {"ok": True}


@app.post("/api/settings/test-provider/{name}")
async def api_test_provider(name: str):
    if name not in providers:
        return {"ok": False, "error": f"Provider '{name}' not available"}
    prov = providers[name]
    try:
        response, usage = await prov.generate(
            [{"role": "user", "content": "Say hello in one sentence."}],
            system="You are a helpful assistant. Be brief.",
        )
        return {"ok": True, "response": response, "model": prov.model,
                "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# --- Telegram webhook ---

@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    """Receive incoming Telegram messages via webhook."""
    if not telegram_bot:
        return {"ok": False, "error": "Telegram not configured"}

    # Verify webhook secret if configured
    if config.TELEGRAM_WEBHOOK_SECRET:
        token = request.headers.get("x-telegram-bot-api-secret-token", "")
        if token != config.TELEGRAM_WEBHOOK_SECRET:
            log.warning("Telegram webhook: invalid secret token")
            return {"ok": False}

    data = await request.json()
    msg = data.get("message", {})
    text = msg.get("text", "")
    chat_id = msg.get("chat", {}).get("id")

    if text and chat_id:
        from . import telegram as tg_module
        task = asyncio.create_task(tg_module.handle_telegram_message(telegram_bot, chat_id, text))
        task.add_done_callback(lambda t: log.error("Telegram handler error: %s", t.exception()) if not t.cancelled() and t.exception() else None)

    return {"ok": True}


# --- WebSocket endpoint ---

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    await manager.send_hello(ws)

    # Each WS connection gets a conversation
    conversation_id = await db.create_conversation()
    # Track active message task so the receive loop stays free for
    # permission_response and other control messages.
    active_task: asyncio.Task | None = None

    def _on_message_done(task: asyncio.Task):
        nonlocal active_task
        active_task = None
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            log.error("Message handler error: %s", exc)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await manager.send_error(ws, "Invalid JSON")
                continue

            msg_type = data.get("type")

            if msg_type == "message":
                # Run in a task so the receive loop stays free to handle
                # permission_response, ping, etc. while the agent loop runs.
                if active_task and not active_task.done():
                    await manager.send_error(ws, "Still processing previous message")
                    continue
                active_task = asyncio.create_task(
                    handle_message(ws, data, conversation_id)
                )
                active_task.add_done_callback(_on_message_done)

            elif msg_type == "set_conversation":
                new_cid = data.get("conversation_id")
                if new_cid:
                    conversation_id = new_cid

            elif msg_type == "permission_response":
                manager.resolve_permission(
                    data.get("id", ""),
                    data.get("granted", False),
                )

            elif msg_type == "ping":
                pass  # keep-alive, no response needed

    except WebSocketDisconnect:
        if active_task and not active_task.done():
            active_task.cancel()
        manager.disconnect(ws)
    except Exception as e:
        log.error("WS error: %s", e)
        if active_task and not active_task.done():
            active_task.cancel()
        manager.disconnect(ws)


# --- Static file serving (Svelte build) ---

WEB_DIST = Path(__file__).parent.parent / "web" / "dist"
if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
