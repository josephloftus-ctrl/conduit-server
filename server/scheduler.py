"""APScheduler-based cron system — runs inside FastAPI event loop."""

import json
import logging
import re
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from . import config, db
from .ws import ConnectionManager

log = logging.getLogger("conduit.scheduler")

_scheduler: AsyncIOScheduler | None = None
_manager: ConnectionManager | None = None


async def start(manager: ConnectionManager):
    """Initialize and start the scheduler."""
    global _scheduler, _manager
    _manager = manager

    _scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)

    # Register all enabled tasks from DB
    tasks = await db.get_scheduled_tasks()
    for task in tasks:
        _register_task(task)

    # Reminder check — configurable interval (default 5 min)
    interval = config.REMINDER_CHECK_MINUTES
    _scheduler.add_job(
        _check_reminders,
        CronTrigger.from_crontab(f"*/{interval} * * * *", timezone=config.TIMEZONE),
        id="reminder_check",
        replace_existing=True,
    )

    # Heartbeat job — runs every HEARTBEAT_INTERVAL minutes
    hb_interval = config.HEARTBEAT_INTERVAL
    _scheduler.add_job(
        _run_heartbeat,
        CronTrigger.from_crontab(f"*/{hb_interval} * * * *", timezone=config.TIMEZONE),
        id="heartbeat",
        replace_existing=True,
    )

    # Email polling job — checks for new unread emails
    if config.OUTLOOK_ENABLED:
        email_interval = config.OUTLOOK_POLL_INTERVAL
        _scheduler.add_job(
            _check_email,
            CronTrigger.from_crontab(f"*/{email_interval} * * * *", timezone=config.TIMEZONE),
            id="email_check",
            replace_existing=True,
        )
        log.info("Email polling enabled every %d min", email_interval)

    # Memory decay — runs daily at 3am, reduces importance of untouched memories
    _scheduler.add_job(
        _run_memory_decay,
        CronTrigger.from_crontab("0 3 * * *", timezone=config.TIMEZONE),
        id="memory_decay",
        replace_existing=True,
    )
    log.info("Memory decay scheduled daily at 3am")

    # Memory consolidation — runs weekly on Sunday at 4am
    _scheduler.add_job(
        _run_memory_consolidation,
        CronTrigger.from_crontab("0 4 * * 0", timezone=config.TIMEZONE),
        id="memory_consolidation",
        replace_existing=True,
    )
    log.info("Memory consolidation scheduled weekly (Sun 4am)")

    # Project indexer — runs daily at 2am
    if config.INDEXER_ENABLED:
        from . import indexer as indexer_mod
        _scheduler.add_job(
            indexer_mod.index_all,
            CronTrigger.from_crontab("0 2 * * *", timezone=config.TIMEZONE),
            id="project_indexer",
            replace_existing=True,
        )
        log.info("Project indexer scheduled daily at 2am")

    _scheduler.start()
    log.info("Scheduler started with %d tasks, reminder check every %d min, heartbeat every %d min",
             len(tasks), interval, hb_interval)


async def stop():
    """Shut down the scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def _register_task(task: dict):
    """Add a DB task to the APScheduler."""
    if not _scheduler:
        return
    try:
        trigger = CronTrigger.from_crontab(task["cron"], timezone=config.TIMEZONE)
        _scheduler.add_job(
            _run_task,
            trigger,
            args=[task["id"], task["name"], task["prompt"], task["model_tier"]],
            id=f"task_{task['id']}",
            replace_existing=True,
        )
        log.info("Registered task: %s (%s)", task["name"], task["cron"])
    except Exception as e:
        log.error("Failed to register task %s: %s", task["name"], e)


async def _run_task(task_id: str, name: str, prompt: str, model_tier: int):
    """Execute a scheduled task and push result to clients + ntfy."""
    if not _manager:
        return

    log.info("Running scheduled task: %s", name)
    await db.update_task_last_run(task_id)

    from .app import get_provider, providers
    from . import router

    # Use NIM for tier 1, Gemini for tier 2, Opus for tier 3
    provider_name = None
    if model_tier >= 3:
        provider_name = await router._try_opus(providers)
    elif model_tier >= 2 and config.LONG_CONTEXT_PROVIDER in providers:
        provider_name = config.LONG_CONTEXT_PROVIDER
    # tier 1 = default (NIM)

    provider = get_provider(provider_name)

    try:
        from .app import render_system_prompt_async
        system = await render_system_prompt_async()
        messages = [{"role": "user", "content": prompt}]
        response, usage = await provider.generate(messages, system=system)
        await db.log_usage(provider.name, provider.model, usage.input_tokens, usage.output_tokens)

        # Push to connected clients
        await _manager.push(content=response, title=name)

        # Push to phone via ntfy
        from . import ntfy
        await ntfy.push(title=name, body=response[:500], tags=["robot"])

        log.info("Task %s completed, pushed to %d clients + ntfy", name, len(_manager.active))
    except Exception as e:
        log.error("Task %s failed: %s", name, e)
        await _manager.push(content=f"Scheduled task failed: {e}", title=f"{name} (Error)")


async def _run_heartbeat():
    """Run heartbeat check — delegates to heartbeat module."""
    try:
        from . import heartbeat
        await heartbeat.check(_manager)
    except ImportError:
        pass  # heartbeat module not yet created
    except Exception as e:
        log.error("Heartbeat error: %s", e)


async def _check_reminders():
    """Check for due reminders in the KV store."""
    raw = await db.kv_get("reminders")
    if not raw:
        return

    reminders = json.loads(raw)
    now = datetime.now().timestamp()
    due = [r for r in reminders if r["due"] <= now]
    remaining = [r for r in reminders if r["due"] > now]

    if due and _manager:
        from . import ntfy

        for r in due:
            # Push to connected clients
            await _manager.push(content=r["text"], title="Reminder")

            # Push to phone
            await ntfy.push(
                title="Reminder",
                body=r["text"],
                tags=["bell"],
                priority=4,
            )

            log.info("Fired reminder: %s", r["text"])

    if remaining != reminders:
        await db.kv_set("reminders", json.dumps(remaining if remaining else []))


async def _check_email():
    """Check for new unread emails and notify if count increased."""
    if not _manager:
        return

    try:
        from . import outlook

        if not outlook.is_configured() or not outlook.get_access_token():
            return

        current_count = await outlook.get_unread_count()
        last_raw = await db.kv_get("outlook_last_unread_count")
        last_count = int(last_raw) if last_raw else 0

        if current_count > last_count:
            new_count = current_count - last_count
            # Fetch the newest unread messages
            messages = await outlook.get_inbox(count=min(new_count, 5), unread_only=True)

            lines = [f"You have {new_count} new email{'s' if new_count > 1 else ''}:"]
            for msg in messages:
                fr = msg.get("from", {}).get("emailAddress", {})
                sender = fr.get("name", fr.get("address", "unknown"))
                subject = msg.get("subject", "(no subject)")
                lines.append(f"- **{subject}** from {sender}")

            body = "\n".join(lines)

            # Push to WebSocket clients
            await _manager.push(content=body, title="New Email")

            # Push to ntfy
            from . import ntfy
            await ntfy.push(
                title="New Email",
                body=body[:500],
                tags=["email"],
                priority=3,
            )

            log.info("Email notification: %d new messages", new_count)

        await db.kv_set("outlook_last_unread_count", str(current_count))

    except Exception as e:
        log.error("Email check error: %s", e)


async def _run_memory_decay():
    """Decay stale memories — reduces importance of untouched memories."""
    try:
        from . import memory
        await memory.decay_memories()
    except Exception as e:
        log.error("Memory decay error: %s", e)


async def _run_memory_consolidation():
    """Consolidate memories — merge duplicates, prune stale entries."""
    try:
        from . import memory
        await memory.consolidate_memories()
    except Exception as e:
        log.error("Memory consolidation error: %s", e)


# --- /remind command parser ---

_AT_PATTERN = re.compile(
    r"(?:remind\s+(?:me\s+)?(?:to\s+)?)?(.+?)\s+at\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)",
    re.IGNORECASE,
)
_IN_PATTERN = re.compile(
    r"(?:remind\s+(?:me\s+)?(?:to\s+)?)?(.+?)\s+in\s+(\d+)\s*(minutes?|hours?|mins?|hrs?)",
    re.IGNORECASE,
)


async def parse_remind(content: str) -> str:
    """Parse a /remind command and schedule it. Returns confirmation text."""
    text = content.lstrip("/").strip()

    # Try "in X minutes/hours"
    m = _IN_PATTERN.search(text)
    if m:
        reminder_text = m.group(1).strip()
        amount = int(m.group(2))
        unit = m.group(3).lower()
        if unit.startswith("h"):
            delta = timedelta(hours=amount)
        else:
            delta = timedelta(minutes=amount)
        due = (datetime.now() + delta).timestamp()
        await _store_reminder(reminder_text, due)
        due_str = datetime.fromtimestamp(due).strftime("%I:%M %p")
        return f"Got it -- I'll remind you to **{reminder_text}** at {due_str}."

    # Try "at HH:MM am/pm"
    m = _AT_PATTERN.search(text)
    if m:
        reminder_text = m.group(1).strip()
        time_str = m.group(2).strip()

        now = datetime.now()
        for fmt in ("%I:%M %p", "%I:%M%p", "%I %p", "%I%p", "%H:%M"):
            try:
                t = datetime.strptime(time_str, fmt)
                due_dt = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
                if due_dt <= now:
                    due_dt += timedelta(days=1)
                due = due_dt.timestamp()
                await _store_reminder(reminder_text, due)
                due_str = due_dt.strftime("%I:%M %p")
                return f"Got it -- I'll remind you to **{reminder_text}** at {due_str}."
            except ValueError:
                continue

    return "Sorry, I couldn't parse that reminder. Try: `/remind check inventory at 3pm` or `/remind stretch in 30 minutes`"


async def _store_reminder(text: str, due: float):
    """Append a reminder to the KV store."""
    raw = await db.kv_get("reminders")
    reminders = json.loads(raw) if raw else []
    reminders.append({"text": text, "due": due})
    await db.kv_set("reminders", json.dumps(reminders))
