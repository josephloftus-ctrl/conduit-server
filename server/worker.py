"""Worker loop — autonomous feature development agent.

State machine that runs on a scheduler, one step per cycle:
  IDLE → SCOUTING → IDEATING → PROPOSING → (wait) →
  PLANNING → PLAN_REVIEW → (wait) → BUILDING → PRESENTING → (wait) → IDLE

Worker state lives in JSON files under data/worker/, separate from main memory.
"""

import json
import logging
import time
from pathlib import Path

from . import config, db

log = logging.getLogger("conduit.worker")

# State constants
IDLE = "IDLE"
SCOUTING = "SCOUTING"
IDEATING = "IDEATING"
PROPOSING = "PROPOSING"
PLANNING = "PLANNING"
PLAN_REVIEW = "PLAN_REVIEW"
BUILDING = "BUILDING"
PRESENTING = "PRESENTING"

_WAITING_STATES = {PROPOSING, PLAN_REVIEW, PRESENTING}
_ALL_STATES = {IDLE, SCOUTING, IDEATING, PROPOSING, PLANNING, PLAN_REVIEW, BUILDING, PRESENTING}

# --- State persistence ---

def _data_dir() -> Path:
    d = Path(config.WORKER_DATA_DIR).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_state() -> dict:
    path = _data_dir() / "state.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log.error("Failed to load worker state: %s", e)
    return {"phase": IDLE, "context": {}, "current_idea": None, "updated_at": time.time()}


def _save_state(state: dict) -> None:
    state["updated_at"] = time.time()
    path = _data_dir() / "state.json"
    path.write_text(json.dumps(state, indent=2))


def _load_ideas() -> list[dict]:
    path = _data_dir() / "ideas.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_ideas(ideas: list[dict]) -> None:
    path = _data_dir() / "ideas.json"
    path.write_text(json.dumps(ideas, indent=2))


def _load_history() -> list[dict]:
    path = _data_dir() / "history.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return []


def _save_history(history: list[dict]) -> None:
    path = _data_dir() / "history.json"
    path.write_text(json.dumps(history, indent=2))


def _read_plan() -> str:
    path = _data_dir() / "active_plan.md"
    return path.read_text() if path.exists() else ""


def _write_plan(content: str) -> None:
    path = _data_dir() / "active_plan.md"
    path.write_text(content)


# --- Public API ---

def is_awaiting_response() -> bool:
    """Check if the worker is waiting for boss input."""
    state = _load_state()
    return state.get("phase") in _WAITING_STATES


def get_status_context() -> str:
    """Return a short status string for the system prompt."""
    state = _load_state()
    phase = state.get("phase", IDLE)
    if phase == IDLE:
        return ""

    idea = state.get("current_idea", {})
    name = idea.get("name", "unnamed feature") if idea else "unnamed feature"

    status_map = {
        SCOUTING: f'Worker status: SCOUTING — gathering context for next feature idea.',
        IDEATING: f'Worker status: IDEATING — brainstorming feature ideas.',
        PROPOSING: f'Worker status: PROPOSING — pitched "{name}", awaiting your response (approve/reject/redirect).',
        PLANNING: f'Worker status: PLANNING — designing implementation for "{name}".',
        PLAN_REVIEW: f'Worker status: PLAN_REVIEW — plan for "{name}" ready for your review.',
        BUILDING: f'Worker status: BUILDING — implementing "{name}".',
        PRESENTING: f'Worker status: PRESENTING — "{name}" is built, awaiting your review.',
    }
    status = status_map.get(phase, f"Worker status: {phase}")

    # Add timing info
    updated = state.get("updated_at", 0)
    if updated:
        age_hours = (time.time() - updated) / 3600
        if age_hours < 1:
            status += f" (updated {int(age_hours * 60)}m ago)"
        elif age_hours < 48:
            status += f" (updated {int(age_hours)}h ago)"
        else:
            status += f" (updated {int(age_hours / 24)}d ago)"

    return status


async def run_cycle(manager) -> None:
    """Main entry — called by scheduler. Loads state, runs one step, saves."""
    state = _load_state()
    phase = state.get("phase", IDLE)
    log.info("Worker cycle starting — phase: %s", phase)

    # Check for proposal timeout
    if phase in _WAITING_STATES:
        updated = state.get("updated_at", 0)
        timeout_hours = config.WORKER_PROPOSAL_TIMEOUT_HOURS
        if updated and (time.time() - updated) > (timeout_hours * 3600):
            log.info("Worker proposal timed out after %dh, shelving", timeout_hours)
            idea = state.get("current_idea", {})
            if idea:
                idea["status"] = "shelved"
                idea["shelved_reason"] = "no response"
                ideas = _load_ideas()
                for i, existing in enumerate(ideas):
                    if existing.get("name") == idea.get("name"):
                        ideas[i] = idea
                        break
                _save_ideas(ideas)
            state["phase"] = IDLE
            state["current_idea"] = None
            _save_state(state)
            return

    # Don't advance if waiting for boss
    if phase in _WAITING_STATES:
        log.info("Worker waiting for boss response in %s phase, skipping", phase)
        return

    dispatch = {
        IDLE: _advance_to_scouting,
        SCOUTING: _scout,
        IDEATING: _ideate,
        PLANNING: _plan,
        BUILDING: _build,
    }

    handler = dispatch.get(phase)
    if handler:
        try:
            state = await handler(state, manager)
            _save_state(state)

            # Send notifications on entry to waiting states
            new_phase = state.get("phase")
            if new_phase == PROPOSING:
                await _propose(state, manager)
        except Exception as e:
            log.error("Worker cycle failed in %s: %s", phase, e, exc_info=True)
    else:
        log.warning("Worker in unhandled phase: %s", phase)


async def handle_boss_response(text: str) -> str | None:
    """Process a boss response to a worker proposal.

    Returns confirmation text if the message was handled, None if not a worker response.
    """
    state = _load_state()
    phase = state.get("phase")

    if phase not in _WAITING_STATES:
        return None

    lower = text.lower().strip()

    if phase == PROPOSING:
        return await _handle_proposal_response(state, lower, text)
    elif phase == PLAN_REVIEW:
        return await _handle_plan_response(state, lower, text)
    elif phase == PRESENTING:
        return await _handle_presentation_response(state, lower, text)

    return None


# --- State handlers ---

async def _advance_to_scouting(state: dict, manager) -> dict:
    """Move from IDLE to SCOUTING."""
    state["phase"] = SCOUTING
    state["context"] = {}
    state["current_idea"] = None
    log.info("Worker advancing to SCOUTING")
    return state


async def _scout(state: dict, manager) -> dict:
    """SCOUTING — gather context from Reddit digest, project indexes, history."""
    context = {}

    # Read Reddit digest
    digest_path = _data_dir() / "reddit_digest.json"
    if digest_path.exists():
        try:
            context["reddit"] = json.loads(digest_path.read_text())
        except (json.JSONDecodeError, OSError):
            context["reddit"] = {}

    # Read project indexes
    index_dir = Path(config.INDEXER_OUTPUT_DIR).expanduser()
    for name in ("spectre", "conduit"):
        idx_path = index_dir / name / "index.json"
        if idx_path.exists():
            try:
                idx = json.loads(idx_path.read_text())
                context[f"project_{name}"] = {
                    "files": len(idx.get("files", [])),
                    "summary": idx.get("summary", ""),
                }
            except (json.JSONDecodeError, OSError):
                pass

    # Read worker history (what was proposed before)
    history = _load_history()
    context["history_count"] = len(history)
    context["rejected_names"] = [
        h["name"] for h in history if h.get("status") == "rejected"
    ]
    context["completed_names"] = [
        h["name"] for h in history if h.get("status") == "completed"
    ]

    # Read recent conversation summaries for context
    try:
        summaries = await db.get_recent_conversations_with_summaries(limit=5)
        if summaries:
            context["recent_conversations"] = [
                s.get("summary", s.get("snippet", ""))[:200] for s in summaries
            ]
    except Exception:
        pass

    state["context"] = context
    state["phase"] = IDEATING
    log.info("Worker scouting complete, advancing to IDEATING")
    return state


async def _ideate(state: dict, manager) -> dict:
    """IDEATING — generate feature ideas using a cheap model."""
    context = state.get("context", {})
    history = _load_history()

    # Build the ideation prompt
    reddit = context.get("reddit", {})
    rejected = context.get("rejected_names", [])
    completed = context.get("completed_names", [])
    boss_feedback = state.get("boss_feedback", "")

    prompt_parts = [
        "You are a developer assistant brainstorming feature ideas for a user's projects.",
        "",
        "The user has two main projects:",
        "- **Spectre**: AI-powered inventory operations dashboard (Python, food service industry)",
        "- **Conduit**: Personal AI assistant platform (Python/FastAPI + Svelte frontend)",
        "",
    ]

    if reddit:
        prompt_parts.append("Recent Reddit activity summary:")
        prompt_parts.append(json.dumps({
            "interests": reddit.get("top_interests", []),
            "hot_topics": reddit.get("hot_topics", []),
            "emerging": reddit.get("emerging_interests", []),
            "mood": reddit.get("mood", ""),
        }, indent=2))
        prompt_parts.append("")

    if completed:
        prompt_parts.append(f"Previously completed features: {', '.join(completed[-5:])}")
    if rejected:
        prompt_parts.append(f"Previously rejected ideas (do NOT re-propose): {', '.join(rejected[-5:])}")
    if boss_feedback:
        prompt_parts.append(f"\nBoss feedback on last proposal: \"{boss_feedback}\"")
        prompt_parts.append("Take this feedback into account when proposing new ideas.")

    prompt_parts.extend([
        "",
        "Propose exactly 3 feature ideas. For each, return a JSON object with:",
        '- "name": short feature name (2-4 words)',
        '- "pitch": one-sentence pitch',
        '- "project": "spectre" or "conduit"',
        '- "effort": "small" (1-2 files), "medium" (3-5 files), or "large" (6+ files)',
        '- "value": "low", "medium", or "high"',
        "",
        "Return a JSON array of 3 objects. Prefer small-to-medium effort, high-value ideas.",
        "Return ONLY valid JSON, no markdown fences.",
    ])

    prompt = "\n".join(prompt_parts)

    from .app import get_provider
    provider = get_provider(config.WORKER_IDEATION_PROVIDER)
    if not provider:
        provider = get_provider(None)

    try:
        response, usage = await provider.generate(
            [{"role": "user", "content": prompt}],
            system="You are a concise product thinker. Return only valid JSON.",
        )
        await db.log_usage(provider.name, provider.model, usage.input_tokens, usage.output_tokens)

        # Parse ideas
        text = response.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        raw_ideas = json.loads(text)
        if not isinstance(raw_ideas, list):
            raw_ideas = [raw_ideas]

    except Exception as e:
        log.error("Ideation failed: %s", e)
        state["phase"] = IDLE
        return state

    # Filter out re-proposals of rejected ideas
    rejected_lower = {n.lower() for n in rejected}
    fresh_ideas = [
        idea for idea in raw_ideas
        if idea.get("name", "").lower() not in rejected_lower
    ]

    if not fresh_ideas:
        log.info("All ideas were duplicates, returning to IDLE")
        state["phase"] = IDLE
        return state

    # Pick the best idea (prefer high value, smaller effort)
    value_rank = {"high": 3, "medium": 2, "low": 1}
    effort_rank = {"small": 3, "medium": 2, "large": 1}
    fresh_ideas.sort(
        key=lambda x: (value_rank.get(x.get("value"), 0), effort_rank.get(x.get("effort"), 0)),
        reverse=True,
    )
    chosen = fresh_ideas[0]
    chosen["status"] = "proposed"
    chosen["proposed_at"] = time.time()

    # Save all ideas to backlog
    ideas = _load_ideas()
    ideas.extend(raw_ideas)
    _save_ideas(ideas)

    state["current_idea"] = chosen
    state["phase"] = PROPOSING
    log.info("Worker ideated, chose: %s", chosen.get("name"))
    return state


async def _propose(state: dict, manager) -> None:
    """PROPOSING — send the pitch to the boss via notifications.

    Called once when entering PROPOSING (from _ideate advancing the state).
    The actual notification is sent here.
    """
    idea = state.get("current_idea", {})
    name = idea.get("name", "unnamed")
    pitch = idea.get("pitch", "No description")
    project = idea.get("project", "unknown")
    effort = idea.get("effort", "unknown")

    body = (
        f"Hey boss, I've been thinking about something.\n\n"
        f"**{name}** ({project}, {effort} effort)\n"
        f"{pitch}\n\n"
        f"Reply: approve / reject / or give me direction"
    )

    # Push via ntfy
    from . import ntfy
    await ntfy.push(title="Worker Proposal", body=body, tags=["bulb", "worker"], priority=3)

    # Push via Telegram if available
    try:
        from . import telegram
        await telegram.push(title="Worker Proposal", body=body)
    except Exception as e:
        log.warning("Worker Telegram push failed: %s", e)

    # Push to WebSocket clients
    if manager:
        await manager.push(content=body, title="Worker Proposal")

    log.info("Worker proposal sent: %s", name)


async def _plan(state: dict, manager) -> dict:
    """PLANNING — design the implementation using a smarter model."""
    idea = state.get("current_idea", {})
    name = idea.get("name", "unnamed")
    pitch = idea.get("pitch", "")
    project = idea.get("project", "conduit")
    boss_feedback = state.get("boss_feedback", "")

    prompt_parts = [
        f"Design an implementation plan for this feature:",
        f"",
        f"**Feature:** {name}",
        f"**Project:** {project}",
        f"**Description:** {pitch}",
    ]

    if boss_feedback:
        prompt_parts.extend(["", f"**Boss feedback:** {boss_feedback}"])

    prompt_parts.extend([
        "",
        "Create a concise implementation plan covering:",
        "1. Files to create or modify (with paths)",
        "2. Key functions/classes to add",
        "3. Integration points with existing code",
        "4. Potential risks or edge cases",
        "5. Testing approach",
        "",
        "Keep it practical and actionable. Use markdown formatting.",
    ])

    from .app import get_provider
    provider = get_provider(config.WORKER_PLANNING_PROVIDER)
    if not provider:
        provider = get_provider(None)

    try:
        response, usage = await provider.generate(
            [{"role": "user", "content": "\n".join(prompt_parts)}],
            system=f"You are a senior developer planning a feature for the {project} project.",
        )
        await db.log_usage(provider.name, provider.model, usage.input_tokens, usage.output_tokens)
    except Exception as e:
        log.error("Planning failed: %s", e)
        state["phase"] = IDLE
        return state

    _write_plan(response)
    state["phase"] = PLAN_REVIEW

    # Send plan for review
    summary = response[:800] if len(response) > 800 else response
    body = f"Plan ready for **{name}**:\n\n{summary}\n\nReply: approve / needs changes / reject"

    from . import ntfy
    await ntfy.push(title="Worker Plan Review", body=body[:500], tags=["clipboard", "worker"], priority=3)

    try:
        from . import telegram
        await telegram.push(title="Worker Plan Review", body=body)
    except Exception:
        pass

    if manager:
        await manager.push(content=body, title="Worker Plan Review")

    log.info("Worker plan ready for review: %s", name)
    return state


async def _build(state: dict, manager) -> dict:
    """BUILDING — implement the feature. Uses claude_code or a strong model."""
    idea = state.get("current_idea", {})
    name = idea.get("name", "unnamed")
    plan = _read_plan()
    boss_feedback = state.get("boss_feedback", "")

    if not plan:
        log.error("No plan found for building phase")
        state["phase"] = IDLE
        return state

    prompt_parts = [
        f"Implement the following feature based on this plan:",
        f"",
        f"**Feature:** {name}",
        f"",
        f"**Plan:**",
        plan,
    ]

    if boss_feedback:
        prompt_parts.extend(["", f"**Additional feedback:** {boss_feedback}"])

    name_slug = name.lower().replace(" ", "-").replace("/", "-")
    prompt_parts.extend([
        "",
        "Implement this feature now. Create or modify the necessary files.",
        f"Work in a git branch named `worker/{name_slug}`.",
    ])

    from .app import get_provider
    building_provider_name = config.WORKER_BUILDING_PROVIDER

    provider = get_provider(building_provider_name)
    if not provider:
        provider = get_provider(None)

    try:
        # If using claude_code provider, use its special run() method
        if hasattr(provider, "manages_own_tools") and provider.manages_own_tools:
            prompt = "\n".join(prompt_parts)
            session_id = await db.kv_get("worker:build_session")
            response, usage, new_session_id, cost = await provider.run(
                prompt, session_id=session_id, ws=None, manager=manager
            )
            if new_session_id:
                await db.kv_set("worker:build_session", new_session_id)
        else:
            response, usage = await provider.generate(
                [{"role": "user", "content": "\n".join(prompt_parts)}],
                system="You are a senior developer implementing a feature. Be precise and thorough.",
            )
        await db.log_usage(provider.name, provider.model, usage.input_tokens, usage.output_tokens)
    except Exception as e:
        log.error("Building failed: %s", e)
        # Don't reset to IDLE — let the cycle retry next time
        return state

    # Advance to presenting
    state["phase"] = PRESENTING
    state["build_output"] = response[:2000]

    # Send presentation
    body = (
        f"I've finished building **{name}**.\n\n"
        f"{response[:800]}\n\n"
        f"Reply: ship it / needs work / reject"
    )

    from . import ntfy
    await ntfy.push(title="Worker: Feature Ready", body=body[:500], tags=["rocket", "worker"], priority=4)

    try:
        from . import telegram
        await telegram.push(title="Worker: Feature Ready", body=body)
    except Exception:
        pass

    if manager:
        await manager.push(content=body, title="Worker: Feature Ready")

    log.info("Worker build complete, presenting: %s", name)
    return state


# --- Boss response handlers ---

async def _handle_proposal_response(state: dict, lower: str, original: str) -> str:
    """Handle response to a PROPOSING state."""
    idea = state.get("current_idea", {})
    name = idea.get("name", "unnamed")

    # Check rejection first — explicit "no" should beat ambiguous "go"
    if _is_rejection(lower) and not _is_approval(lower):
        idea["status"] = "rejected"
        ideas = _load_ideas()
        for i, existing in enumerate(ideas):
            if existing.get("name") == idea.get("name"):
                ideas[i] = idea
                break
        _save_ideas(ideas)
        _add_to_history(idea)
        state["phase"] = IDLE
        state["current_idea"] = None
        _save_state(state)
        return f"Got it, shelving **{name}**. I'll come up with something else next cycle."

    elif _is_approval(lower):
        state["phase"] = PLANNING
        state["boss_feedback"] = ""
        _save_state(state)
        await _notify("Worker: Approved", f"Got it, planning **{name}** now.", ["white_check_mark"])
        return f"Approved! I'll start planning **{name}**."

    else:
        # Treat as feedback/redirect — go back to ideating with constraints
        state["phase"] = IDEATING
        state["boss_feedback"] = original
        _save_state(state)
        return f"Noted. I'll rethink with your feedback: \"{original[:100]}\""


async def _handle_plan_response(state: dict, lower: str, original: str) -> str:
    """Handle response to a PLAN_REVIEW state."""
    idea = state.get("current_idea", {})
    name = idea.get("name", "unnamed")

    if _is_rejection(lower) and not _is_approval(lower):
        _add_to_history(idea)
        state["phase"] = IDLE
        state["current_idea"] = None
        _save_state(state)
        return f"Plan rejected. Shelving **{name}**."

    elif _is_approval(lower):
        state["phase"] = BUILDING
        state["boss_feedback"] = ""
        _save_state(state)
        await _notify("Worker: Plan Approved", f"Starting build on **{name}**.", ["hammer_and_wrench"])
        return f"Plan approved. Building **{name}** now."

    else:
        state["phase"] = PLANNING
        state["boss_feedback"] = original
        _save_state(state)
        return f"Got it, revising the plan with your feedback."


async def _handle_presentation_response(state: dict, lower: str, original: str) -> str:
    """Handle response to a PRESENTING state."""
    idea = state.get("current_idea", {})
    name = idea.get("name", "unnamed")

    if _is_rejection(lower) and not _is_ship(lower):
        idea["status"] = "rejected"
        _add_to_history(idea)
        state["phase"] = IDLE
        state["current_idea"] = None
        _save_state(state)
        return f"Understood. Discarding **{name}**."

    elif _is_ship(lower):
        idea["status"] = "completed"
        idea["completed_at"] = time.time()
        _add_to_history(idea)
        state["phase"] = IDLE
        state["current_idea"] = None
        state["build_output"] = ""
        _save_state(state)
        await _notify("Worker: Shipped", f"**{name}** is done!", ["ship"])
        return f"Shipped! **{name}** is complete."

    else:
        # Needs work
        state["phase"] = BUILDING
        state["boss_feedback"] = original
        _save_state(state)
        return f"Going back to fix things based on your feedback."


# --- Helpers ---

def _match_any(text: str, phrases: set[str]) -> bool:
    """Check if text contains any phrase, using word boundaries for single words."""
    import re
    for phrase in phrases:
        if " " in phrase:
            # Multi-word: substring match is fine
            if phrase in text:
                return True
        else:
            # Single word: use word boundary to avoid false positives
            if re.search(rf"\b{re.escape(phrase)}\b", text):
                return True
    return False


def _is_approval(text: str) -> bool:
    approvals = {"approved", "approve", "yes", "go", "do it", "lgtm", "looks good", "go ahead", "ship it", "ok", "yep", "sure"}
    return _match_any(text, approvals)


def _is_rejection(text: str) -> bool:
    rejections = {"no", "reject", "rejected", "bad idea", "nah", "pass", "skip", "cancel", "stop"}
    return _match_any(text, rejections)


def _is_ship(text: str) -> bool:
    ships = {"ship it", "ship", "merge", "deploy", "looks good", "lgtm", "done", "great", "perfect"}
    return _match_any(text, ships)


def _add_to_history(idea: dict) -> None:
    """Add an idea to the history file."""
    history = _load_history()
    history.append(idea)
    # Keep last 50
    if len(history) > 50:
        history = history[-50:]
    _save_history(history)


async def _notify(title: str, body: str, tags: list[str] | None = None) -> None:
    """Push a notification via ntfy + Telegram."""
    from . import ntfy
    await ntfy.push(title=title, body=body, tags=tags or [], priority=3)
    try:
        from . import telegram
        await telegram.push(title=title, body=body)
    except Exception:
        pass
