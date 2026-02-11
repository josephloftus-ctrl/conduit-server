"""Telegram bot integration — two-way messaging via Bot API webhooks."""

import asyncio
import logging

import httpx

from . import config, db

log = logging.getLogger("conduit.telegram")

API_BASE = "https://api.telegram.org/bot{token}"


class TelegramBot:
    """Thin async wrapper around the Telegram Bot API."""

    def __init__(self, token: str):
        self.token = token
        self.base = API_BASE.format(token=token)

    async def send_message(self, chat_id: int | str, text: str,
                           parse_mode: str = "Markdown") -> dict | None:
        """Send a text message to a chat."""
        # Telegram Markdown can choke on unmatched special chars — fall back to plain
        payload = {"chat_id": chat_id, "text": text[:4096], "parse_mode": parse_mode}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{self.base}/sendMessage", json=payload)
                if resp.status_code == 200:
                    return resp.json()
                # Retry without parse_mode on formatting errors
                if resp.status_code == 400 and "parse" in resp.text.lower():
                    payload.pop("parse_mode")
                    resp = await client.post(f"{self.base}/sendMessage", json=payload)
                    return resp.json() if resp.status_code == 200 else None
                log.warning("sendMessage failed (%d): %s", resp.status_code, resp.text)
        except Exception as e:
            log.error("Telegram send error: %s", e)
        return None

    async def send_chat_action(self, chat_id: int | str, action: str = "typing"):
        """Show a typing indicator or other chat action."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(f"{self.base}/sendChatAction",
                                  json={"chat_id": chat_id, "action": action})
        except Exception:
            pass  # best-effort

    async def set_webhook(self, url: str, secret_token: str = ""):
        """Register the webhook URL with Telegram."""
        try:
            payload = {"url": url}
            if secret_token:
                payload["secret_token"] = secret_token
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(f"{self.base}/setWebhook", json=payload)
                if resp.status_code == 200:
                    log.info("Telegram webhook set: %s", url)
                else:
                    log.warning("setWebhook failed (%d): %s", resp.status_code, resp.text)
        except Exception as e:
            log.error("Telegram setWebhook error: %s", e)

    async def delete_webhook(self):
        """Remove the webhook."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(f"{self.base}/deleteWebhook")
        except Exception as e:
            log.error("Telegram deleteWebhook error: %s", e)

    async def push(self, title: str = "", body: str = ""):
        """Convenience: send a notification-style message to the configured chat."""
        if not config.TELEGRAM_ENABLED or not config.TELEGRAM_CHAT_ID:
            return
        text = f"*{title}*\n\n{body}" if title else body
        await self.send_message(config.TELEGRAM_CHAT_ID, text[:4000])


async def push(title: str = "", body: str = ""):
    """Module-level push — sends to the configured chat via the app's bot instance.

    Mirrors ntfy.push() for use in heartbeat.py and other fire-and-forget contexts.
    """
    if not config.TELEGRAM_ENABLED or not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return
    try:
        from .app import telegram_bot
        if telegram_bot:
            await telegram_bot.push(title=title, body=body)
    except Exception as e:
        log.error("Telegram push error: %s", e)


class TelegramAdapter:
    """Mimics ConnectionManager's send_* interface, buffering output for Telegram.

    Passed as `manager` to providers/agent loop so they can emit chunks that
    get accumulated and sent as a single Telegram message at the end.
    """

    def __init__(self, bot: TelegramBot, chat_id: int | str):
        self.bot = bot
        self.chat_id = chat_id
        self.chunks: list[str] = []

    async def send_chunk(self, ws, content: str):
        self.chunks.append(content)

    async def send_done(self, ws):
        pass  # we send the full message after the pipeline completes

    async def send_meta(self, ws, model: str = "", input_tokens: int = 0, output_tokens: int = 0):
        pass  # skip token counts in Telegram

    async def send_typing(self, ws):
        await self.bot.send_chat_action(self.chat_id, "typing")

    async def send_error(self, ws, message: str):
        await self.bot.send_message(self.chat_id, f"Error: {message}")

    async def send_tool_start(self, ws, tool_call_id: str, name: str, arguments: dict):
        pass  # skip tool details in Telegram

    async def send_tool_done(self, ws, tool_call_id: str, name: str,
                             result: str = "", error: str = ""):
        pass  # skip tool details in Telegram

    async def request_permission(self, ws, action: str, detail: dict) -> bool:
        # Auto-approve is checked centrally in agent.py before this is called.
        # If we get here, auto-approve is OFF — allow reads, deny write/execute.
        if config.AUTO_APPROVE_READS and action.startswith("read"):
            return True
        return False

    def get_response(self) -> str:
        return "".join(self.chunks)


async def handle_telegram_message(bot: TelegramBot, chat_id: int, text: str):
    """Process an incoming Telegram message — classify, route, respond."""
    # Security: only accept messages from the configured chat
    if config.TELEGRAM_CHAT_ID and str(chat_id) != config.TELEGRAM_CHAT_ID:
        log.warning("Ignoring message from unauthorized chat_id: %s", chat_id)
        return

    # Log chat_id for initial setup (before chat_id is configured)
    if not config.TELEGRAM_CHAT_ID:
        log.info("Telegram message from chat_id: %s — set this in config.yaml", chat_id)
        await bot.send_message(chat_id, f"Your chat ID is `{chat_id}`. Set this in config.yaml to enable me.")
        return

    await bot.send_chat_action(chat_id, "typing")

    # Track user activity
    from datetime import datetime
    await db.kv_set("last_user_activity", str(datetime.now().timestamp()))

    # Get or create a conversation for this Telegram chat
    conv_key = f"tg_conv:{chat_id}"
    conversation_id = await db.kv_get(conv_key)
    if not conversation_id:
        conversation_id = await db.create_conversation("Telegram")
        await db.kv_set(conv_key, conversation_id)

    # Store user message
    await db.add_message(conversation_id, "user", text)

    # Handle /commands
    if text.startswith("/"):
        response = await _handle_telegram_command(text, conversation_id, chat_id, bot)
        if response is not None:
            await bot.send_message(chat_id, response)
            if response:
                await db.add_message(conversation_id, "assistant", response)
            return

    # Lazy-load modules (same pattern as app.py)
    from .app import get_provider, render_system_prompt_async, providers as app_providers, stream_with_fallback
    from .tools import get_all as get_all_tools

    router_module = None
    memory_module = None
    try:
        from . import router as rm
        router_module = rm
    except Exception:
        pass
    try:
        from . import memory as mem_mod
        memory_module = mem_mod
    except Exception:
        pass

    # Build message history
    history = await db.get_messages(conversation_id, limit=50)
    conversation_length = len(history)

    # Route
    provider_name = None
    if router_module:
        provider_name = await router_module.route(text, app_providers, conversation_length)

    # Strip /command prefix before sending to model
    model_content = text
    if router_module:
        model_content = router_module.strip_command(text)

    messages = []
    for msg in history[:-1]:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": model_content})

    system = await render_system_prompt_async()
    adapter = TelegramAdapter(bot, chat_id)

    try:
        selected_provider = get_provider(provider_name)
        tools = get_all_tools()

        if getattr(selected_provider, 'manages_own_tools', False):
            session_key = f"cc_session:{conversation_id}"
            session_id = await db.kv_get(session_key)
            response_text, usage, new_session_id, cost_usd = await selected_provider.run(
                prompt=model_content, session_id=session_id, ws=None, manager=adapter,
            )
            if new_session_id and new_session_id != session_id:
                await db.kv_set(session_key, new_session_id)
            provider = selected_provider
        elif selected_provider.supports_tools and config.TOOLS_ENABLED and tools:
            from . import agent
            response_text, usage = await agent.run_agent_loop(
                messages, system, selected_provider, tools, None, adapter,
                max_turns=config.MAX_AGENT_TURNS,
            )
            provider = selected_provider
        else:
            response_text, usage, provider = await stream_with_fallback(
                messages, system, provider_name=provider_name, ws=None,
            )
    except Exception as e:
        log.error("Telegram message handling failed: %s", e)
        await bot.send_message(chat_id, f"Something went wrong: {e}")
        return

    # Combine adapter buffer with any direct response_text
    final_text = adapter.get_response() or response_text
    if not final_text:
        final_text = "(No response generated)"

    # Send response
    await bot.send_message(chat_id, final_text[:4096])

    # Log usage
    if usage:
        await db.log_usage(provider.name, provider.model, usage.input_tokens, usage.output_tokens)

    # Store assistant message
    if final_text:
        await db.add_message(conversation_id, "assistant", final_text,
                             model=provider.model, source=provider.name)

    # Background: extract memories
    if memory_module and config.EXTRACTION_ENABLED:
        asyncio.create_task(
            memory_module.extract_memories(text, final_text, conversation_id)
        )


async def _handle_telegram_command(text: str, conversation_id: str,
                                   chat_id: int, bot: TelegramBot) -> str | None:
    """Handle /commands in Telegram. Returns response text, or None if not a command."""
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()

    # Telegram sends /start on first interaction
    if cmd == "/start":
        return f"Hey! I'm {config.PERSONALITY_NAME}. Your chat ID is `{chat_id}`. Send me anything."

    if cmd == "/clear":
        new_id = await db.create_conversation("Telegram")
        await db.kv_set(f"tg_conv:{chat_id}", new_id)
        return "Conversation cleared."

    if cmd == "/usage":
        tokens = await db.get_daily_opus_tokens()
        budget = config.OPUS_DAILY_BUDGET
        haiku_tokens = await db.get_daily_provider_tokens("haiku")
        cc_tokens = await db.get_daily_provider_tokens("claude_code")
        return (
            f"*Opus*: {tokens:,} / {budget:,} output tokens today\n"
            f"*Haiku*: {haiku_tokens:,} output tokens today\n"
            f"*Claude Code*: {cc_tokens:,} output tokens today"
        )

    if cmd == "/memories":
        try:
            from . import memory as mem_mod
            memories = await mem_mod.get_all_memories()
            if memories:
                lines = [f"*Memories ({len(memories)}):*"]
                for m in memories[:15]:
                    lines.append(f"- [{m['category']}] {m['content']}")
                if len(memories) > 15:
                    lines.append(f"_...and {len(memories) - 15} more_")
                return "\n".join(lines)
            return "No memories stored yet."
        except Exception:
            return "Memory system not available."

    if cmd == "/permissions":
        override = await db.kv_get("auto_approve_tools")
        if override is not None:
            is_on = override == "true"
        else:
            is_on = config.AUTO_APPROVE_ALL
        new_val = not is_on
        await db.kv_set("auto_approve_tools", "true" if new_val else "false")
        status = "ON — all tools auto-approved" if new_val else "OFF — reads only, writes/execute denied"
        return f"Tool permissions: {status}"

    if cmd == "/model":
        from .app import providers as app_providers
        or_provider = app_providers.get("openrouter")
        if not or_provider:
            return "OpenRouter not configured."
        arg = parts[1].strip() if len(parts) > 1 else ""
        if not arg:
            return f"Active: `{or_provider.model}`\nUsage: /model <model-id> or /model reset"
        if arg == "reset":
            default = config.PROVIDERS.get("openrouter", {}).get("default_model", "openrouter/free")
            or_provider.model = default
            await db.kv_set("openrouter_model", "")
            return f"Reset to `{default}`"
        or_provider.model = arg
        await db.kv_set("openrouter_model", arg)
        return f"OpenRouter model: `{arg}`"

    if cmd == "/help":
        return "\n".join([
            "*Commands:*",
            "/clear - new conversation",
            "/usage - token budget status",
            "/memories - view stored memories",
            "/permissions - toggle tool auto-approve",
            "/model - switch OpenRouter model",
            "/help - this message",
            "",
            "Or just type naturally. Use prefixes:",
            "/or - use OpenRouter",
            "/opus - deep thinking",
            "/research - use Gemini",
            "/code - use Claude Code",
        ])

    # Not a recognized standalone command — pass through to model routing
    # (commands like /opus, /code, /research are model prefixes, not commands)
    return None
