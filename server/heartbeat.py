"""Heartbeat system — proactive check-ins via WebSocket + ntfy."""

import json
import logging
from datetime import datetime

from . import config, db, ntfy, spectre, telegram as tg_module
from .ws import ConnectionManager

log = logging.getLogger("conduit.heartbeat")

# Track what we've sent today to avoid duplicates
_sent_today: dict[str, str] = {}  # type → date string


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _in_active_hours() -> bool:
    """Check if current time is within active hours."""
    hour = datetime.now().hour
    start, end = config.ACTIVE_HOURS
    return start <= hour < end


def _reset_daily():
    """Reset daily tracking if date changed."""
    global _sent_today
    today = _today()
    if _sent_today.get("_date") != today:
        _sent_today = {"_date": today}


async def _get_idle_minutes() -> float:
    """Get minutes since last user activity."""
    raw = await db.kv_get("last_user_activity")
    if not raw:
        return float("inf")
    try:
        last = float(raw)
        return (datetime.now().timestamp() - last) / 60
    except (ValueError, TypeError):
        return float("inf")


async def check(manager: ConnectionManager):
    """Main heartbeat check — called by scheduler every HEARTBEAT_INTERVAL minutes."""
    await _dispatch_plugin_heartbeat(manager)

    if not _in_active_hours():
        return

    _reset_daily()
    now = datetime.now()
    hour = now.hour
    today = _today()

    # Morning heartbeat: 7-8am workdays
    if 7 <= hour < 8 and now.weekday() < 5:
        if _sent_today.get("morning") != today:
            await _morning_heartbeat(manager)
            _sent_today["morning"] = today
            return

    # Evening recap: 6-7pm workdays
    if 18 <= hour < 19 and now.weekday() < 5:
        if _sent_today.get("evening") != today:
            await _evening_heartbeat(manager)
            _sent_today["evening"] = today
            return

    # Threshold alerts (every cycle)
    await _check_thresholds(manager)

    # Idle check-in: if user has been idle for IDLE_CHECKIN_MINUTES
    idle_minutes = await _get_idle_minutes()
    if idle_minutes >= config.IDLE_CHECKIN_MINUTES:
        last_checkin = _sent_today.get("idle_time", "")
        # Don't check in more than once per idle window
        if last_checkin != today or _sent_today.get("idle_count", 0) < 3:
            await _idle_heartbeat(manager, idle_minutes)
            _sent_today["idle_time"] = today
            _sent_today["idle_count"] = _sent_today.get("idle_count", 0) + 1
            # Reset idle tracking so it doesn't fire again until activity + idle
            await db.kv_set("last_user_activity", str(datetime.now().timestamp()))


async def _dispatch_plugin_heartbeat(manager: ConnectionManager):
    """Run plugin heartbeat hooks once per scheduler tick."""
    try:
        from .plugins import dispatch_hook
        await dispatch_hook(
            "heartbeat_tick",
            manager=manager,
            sent_today=dict(_sent_today),
            now=datetime.now().isoformat(),
        )
    except Exception as e:
        log.debug("heartbeat_tick hook dispatch failed: %s", e)


async def _morning_heartbeat(manager: ConnectionManager):
    """Morning check-in with context."""
    log.info("Sending morning heartbeat")

    from .app import get_provider, render_system_prompt_async

    # Gather context
    try:
        from . import memory as memory_module
        memories = await memory_module.get_all_memories()
        memories = memories[:10]
    except Exception:
        memories = []
    recent_convs = await db.get_recent_conversations_with_summaries(limit=3)

    context_parts = []
    if memories:
        mem_text = ", ".join(m["content"] for m in memories[:5])
        context_parts.append(f"Key memories: {mem_text}")
    if recent_convs:
        conv_text = "; ".join(
            f"{c['title']}: {c['summary'][:100]}" for c in recent_convs
        )
        context_parts.append(f"Recent conversations: {conv_text}")

    # Get pending reminders
    raw = await db.kv_get("reminders")
    if raw:
        reminders = json.loads(raw)
        active = [r for r in reminders if r["due"] > datetime.now().timestamp()]
        if active:
            rem_text = ", ".join(r["text"] for r in active[:5])
            context_parts.append(f"Pending reminders: {rem_text}")

    # Pull Spectre operational data (graceful skip if offline)
    try:
        inv_summary = await spectre.get_inventory_summary()
        if inv_summary:
            parts = []
            if "site_count" in inv_summary:
                parts.append(f"{inv_summary['site_count']} sites tracked")
            if "total_value" in inv_summary:
                parts.append(f"${inv_summary['total_value']:,.0f} total inventory value")
            if "flagged_items" in inv_summary:
                parts.append(f"{inv_summary['flagged_items']} flagged items")
            if parts:
                context_parts.append(f"Spectre inventory: {', '.join(parts)}")

        lm100_score = await spectre.get_site_score("lockhead_martin_bldg_100")
        if lm100_score:
            parts = []
            if "score" in lm100_score:
                parts.append(f"health score {lm100_score['score']}")
            if "status" in lm100_score:
                parts.append(lm100_score["status"])
            if "delta" in lm100_score:
                d = lm100_score["delta"]
                parts.append(f"{'up' if d >= 0 else 'down'} {abs(d)} from last period")
            if parts:
                context_parts.append(f"LM Building 100: {', '.join(parts)}")
    except Exception as e:
        log.debug("Spectre data unavailable for morning heartbeat: %s", e)

    context = "\n".join(context_parts) if context_parts else "No specific context."

    prompt = (
        f"Give a brief, friendly morning check-in. Today is {datetime.now().strftime('%A, %B %d')}.\n"
        f"Context about the user:\n{context}\n\n"
        "Keep it warm and concise (under 150 words). Mention any relevant reminders or follow-ups "
        "from recent conversations. If there's operational data from Spectre, mention it naturally. "
        "Don't be generic — reference specific things you know."
    )

    provider = get_provider()  # NIM — free
    try:
        system = await render_system_prompt_async()
        response, usage = await provider.generate(
            [{"role": "user", "content": prompt}],
            system=system,
        )
        await db.log_usage(provider.name, provider.model, usage.input_tokens, usage.output_tokens)

        # Push to WS clients
        await manager.push(content=response, title="Good Morning")

        # Push to phone
        await ntfy.push(
            title="Good Morning",
            body=response[:400],
            tags=["sunrise"],
            priority=3,
        )
        await tg_module.push(title="Good Morning", body=response[:4000])

        log.info("Morning heartbeat sent")
    except Exception as e:
        log.error("Morning heartbeat failed: %s", e)


async def _evening_heartbeat(manager: ConnectionManager):
    """Evening recap of the day."""
    log.info("Sending evening heartbeat")

    from .app import get_provider, render_system_prompt_async

    # Get today's conversations
    recent_convs = await db.get_recent_conversations_with_summaries(limit=10)
    today_str = _today()

    today_convs = []
    for c in recent_convs:
        conv_date = datetime.fromtimestamp(c["updated_at"]).strftime("%Y-%m-%d")
        if conv_date == today_str:
            today_convs.append(c)

    # Get usage stats
    usage_stats = await db.get_usage_by_provider(days=1)

    context_parts = []
    if today_convs:
        conv_text = "; ".join(
            f"{c['title']}: {c['summary'][:100]}" for c in today_convs
        )
        context_parts.append(f"Today's conversations: {conv_text}")
    else:
        context_parts.append("No conversations today.")

    if usage_stats:
        usage_text = ", ".join(
            f"{u['provider']}: {u['total_output']:,} tokens" for u in usage_stats
        )
        context_parts.append(f"Token usage today: {usage_text}")

    context = "\n".join(context_parts)

    prompt = (
        f"Give a brief evening recap. Today is {datetime.now().strftime('%A, %B %d')}.\n"
        f"Context:\n{context}\n\n"
        "Summarize what we accomplished today, note any loose threads or follow-ups. "
        "Keep it concise (under 150 words)."
    )

    provider = get_provider()  # NIM — free
    try:
        system = await render_system_prompt_async()
        response, usage = await provider.generate(
            [{"role": "user", "content": prompt}],
            system=system,
        )
        await db.log_usage(provider.name, provider.model, usage.input_tokens, usage.output_tokens)

        await manager.push(content=response, title="Evening Recap")
        await ntfy.push(
            title="Evening Recap",
            body=response[:400],
            tags=["moon"],
            priority=2,
        )
        await tg_module.push(title="Evening Recap", body=response[:4000])

        log.info("Evening heartbeat sent")
    except Exception as e:
        log.error("Evening heartbeat failed: %s", e)


async def _idle_heartbeat(manager: ConnectionManager, idle_minutes: float):
    """Contextual check-in after user has been idle."""
    log.info("Sending idle check-in (%.0f min idle)", idle_minutes)

    from .app import get_provider, render_system_prompt_async

    # Get recent context
    recent_convs = await db.get_recent_conversations_with_summaries(limit=3)
    try:
        from . import memory as memory_module
        memories = await memory_module.get_all_memories()
        memories = memories[:5]
    except Exception:
        memories = []

    context_parts = []
    if recent_convs:
        conv_text = "; ".join(
            f"{c['title']}: {c['summary'][:80]}" for c in recent_convs[:2]
        )
        context_parts.append(f"Recent: {conv_text}")
    if memories:
        mem_text = ", ".join(m["content"] for m in memories[:3])
        context_parts.append(f"Memories: {mem_text}")

    context = "\n".join(context_parts) if context_parts else "No recent context."

    hours = idle_minutes / 60
    prompt = (
        f"The user has been away for about {hours:.1f} hours. "
        f"Send a brief, casual check-in message. Don't be clingy or annoying.\n"
        f"Context:\n{context}\n\n"
        "Keep it to 1-2 sentences. Reference something specific from recent conversations "
        "or memories if relevant. Be natural."
    )

    provider = get_provider()  # NIM — free
    try:
        system = await render_system_prompt_async()
        response, usage = await provider.generate(
            [{"role": "user", "content": prompt}],
            system=system,
        )
        await db.log_usage(provider.name, provider.model, usage.input_tokens, usage.output_tokens)

        await manager.push(content=response, title="Check-in")
        await ntfy.push(
            title="Conduit",
            body=response[:300],
            tags=["wave"],
            priority=2,
        )
        await tg_module.push(title="Check-in", body=response[:4000])

        log.info("Idle check-in sent")
    except Exception as e:
        log.error("Idle check-in failed: %s", e)


async def _check_thresholds(manager: ConnectionManager):
    """Check Spectre health scores against configured thresholds.

    Uses KV-based cooldown to prevent alert spam.
    """
    # Quick bail if Spectre is unreachable
    if not await spectre.health_check():
        return

    now = datetime.now().timestamp()
    cooldown_seconds = config.ALERT_COOLDOWN_MINUTES * 60

    # Check site health scores
    for site_id in ("lockhead_martin_bldg_100",):
        score_data = await spectre.get_site_score(site_id)
        if not score_data or "score" not in score_data:
            continue

        score = score_data["score"]
        if score >= config.HEALTH_SCORE_MINIMUM:
            continue

        # Check cooldown
        cooldown_key = f"threshold_alert:health:{site_id}"
        last_alert = await db.kv_get(cooldown_key)
        if last_alert:
            try:
                if now - float(last_alert) < cooldown_seconds:
                    continue
            except (ValueError, TypeError):
                pass

        # Fire alert
        site_name = score_data.get("site_name", site_id)
        body = (
            f"{site_name} health score dropped to {score} "
            f"(minimum: {config.HEALTH_SCORE_MINIMUM})."
        )
        if "status" in score_data:
            body += f" Status: {score_data['status']}."

        await manager.push(content=f"**Health Alert**\n{body}", title="Health Alert")
        await ntfy.push(
            title="Health Alert",
            body=body,
            tags=["warning"],
            priority=4,
        )
        await tg_module.push(title="Health Alert", body=body)

        await db.kv_set(cooldown_key, str(now))
        log.warning("Threshold alert: %s health score %s", site_name, score)
