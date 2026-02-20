"""Apple TV plugin — remote control tool + heartbeat state monitoring.

Hooks:
- heartbeat_tick: polls TV state every tick, handles auto-off and idle detection
- before_agent_start: injects current TV state into system prompt
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from datetime import datetime

from server.plugins import PluginAPI

log = logging.getLogger("conduit.plugin.apple_tv")

# ---------------------------------------------------------------------------
# Defaults (overridable via plugin config)
# ---------------------------------------------------------------------------
_DEVICE_NAME = "Living Room"
_ATVREMOTE_PATH = "atvremote"
_TIMEOUT_SECONDS = 15
_AUTO_OFF_HOUR = 23  # 11pm
_IDLE_TIMEOUT_MINUTES = 120
_AUTO_OFF_ENABLED = True

# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------
_tv_state: dict = {}
_idle_nudge_sent_date: str = ""

# ---------------------------------------------------------------------------
# Action whitelist
# ---------------------------------------------------------------------------
SIMPLE_ACTIONS = frozenset({
    # Navigation
    "up", "down", "left", "right", "select", "menu", "home",
    "home_hold", "top_menu", "control_center",
    # Playback
    "play", "pause", "play_pause", "stop", "next", "previous",
    # Volume
    "volume_up", "volume_down",
    # Power
    "turn_on", "turn_off", "power_state",
    # Info
    "playing", "title", "artist", "album", "app", "device_state",
    "position", "total_time", "media_type", "volume",
    # Other
    "app_list", "text_get", "text_clear", "screensaver",
    # Seek (no value = default skip)
    "skip_forward", "skip_backward",
})

VALUE_ACTIONS = frozenset({
    "set_volume", "launch_app", "text_set", "text_append",
    "set_position", "set_repeat", "set_shuffle",
    # Seek with custom seconds
    "skip_forward", "skip_backward",
})

ALL_ACTIONS = SIMPLE_ACTIONS | VALUE_ACTIONS


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------
async def _run_atvremote(action: str, value: str | None = None) -> str:
    """Run an atvremote command and return stdout."""
    device = shlex.quote(_DEVICE_NAME)
    cmd = f"{_ATVREMOTE_PATH} -n {device} {action}"
    if value is not None:
        cmd += f"={shlex.quote(str(value))}"

    log.debug("atvremote: %s", cmd)
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return f"Error: atvremote timed out after {_TIMEOUT_SECONDS}s"

        out = stdout.decode().strip()
        err = stderr.decode().strip()

        if proc.returncode != 0:
            if "no device found" in err.lower():
                return "Error: Apple TV not found on the network. Is it powered on and on the same LAN?"
            if "not paired" in err.lower() or "auth" in err.lower():
                return "Error: Apple TV pairing issue. May need to re-pair with `atvremote wizard`."
            return f"Error: {err or out or f'exit code {proc.returncode}'}"

        return out if out else "OK"

    except FileNotFoundError:
        return f"Error: atvremote not found at '{_ATVREMOTE_PATH}'. Is pyatv installed?"
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------
async def _apple_tv(action: str, value: str | None = None) -> str:
    """Execute an Apple TV remote action."""
    action = action.strip().lower()

    if action not in ALL_ACTIONS:
        available = ", ".join(sorted(ALL_ACTIONS))
        return f"Unknown action '{action}'. Available actions: {available}"

    if value is not None and action not in VALUE_ACTIONS:
        return f"Action '{action}' does not accept a value."

    if action in VALUE_ACTIONS and action not in SIMPLE_ACTIONS and value is None:
        return f"Action '{action}' requires a value."

    return await _run_atvremote(action, value)


# ---------------------------------------------------------------------------
# Heartbeat tick — polls state, auto-off, idle detection
# ---------------------------------------------------------------------------
async def _heartbeat_tick(**kwargs):
    global _tv_state, _idle_nudge_sent_date

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # Reset idle nudge tracking on new day
    if _idle_nudge_sent_date and _idle_nudge_sent_date != today:
        _idle_nudge_sent_date = ""

    # Poll device state + app to determine if TV is on.
    # NOTE: power_state is broken on tvOS 18.x (FetchAttentionState removed).
    # Instead we use device_state + app as a proxy: if we get a valid response,
    # the TV is on. If we get an error/timeout, it's off or unreachable.
    device_state_raw = await _run_atvremote("device_state")
    if device_state_raw.startswith("Error"):
        # Could be off (unreachable) or actually unreachable
        log.debug("Apple TV heartbeat: %s", device_state_raw)
        _tv_state = {
            "power": "off",
            "updated_at": now.isoformat(),
        }
        return {"status": "off_or_unreachable", "error": device_state_raw}

    # Got a response — TV is on
    is_on = True
    device_state = device_state_raw.strip()

    state: dict = {
        "power": "on",
        "device_state": device_state,
        "updated_at": now.isoformat(),
    }

    # Grab playback info and current app
    app_raw = await _run_atvremote("app")
    title_raw = await _run_atvremote("playing")

    state["app"] = app_raw if not app_raw.startswith("Error") else None
    state["playing_raw"] = title_raw if not title_raw.startswith("Error") else None

    # Parse playing output for title/position
    if state.get("playing_raw"):
        state.update(_parse_playing(state["playing_raw"]))

    _tv_state = state

    # Auto-off: TV on past bedtime
    if _AUTO_OFF_ENABLED and is_on and now.hour >= _AUTO_OFF_HOUR:
        log.info("Auto-off: turning off Apple TV (hour=%d, threshold=%d)", now.hour, _AUTO_OFF_HOUR)
        result = await _run_atvremote("turn_off")
        _tv_state["power"] = "off"
        _tv_state["auto_off"] = True

        try:
            from server import ntfy
            await ntfy.push(
                title="Apple TV Auto-Off",
                body=f"Turned off {_DEVICE_NAME} Apple TV (bedtime auto-off, {now.strftime('%I:%M %p')}).",
                tags=["tv", "zzz"],
                priority=2,
            )
        except Exception as e:
            log.debug("ntfy push failed: %s", e)

        return {"status": "auto_off", "result": result}

    # Idle detection: on home screen with no media for too long
    if is_on and _idle_nudge_sent_date != today:
        device_st = state.get("device_state", "")
        title = state.get("title")
        # "idle" or on home screen with no active playback
        is_idle = (
            device_st and "idle" in device_st.lower()
        ) or (not title and device_st and "playing" not in device_st.lower())

        if is_idle:
            # Check how long we've been idle by comparing with previous state
            prev_power = kwargs.get("sent_today", {}).get("_date", "")
            # Simple approach: if TV is idle right now, send a nudge once per day
            log.info("Apple TV idle on home screen — sending nudge")
            _idle_nudge_sent_date = today

            try:
                from server import ntfy
                await ntfy.push(
                    title="Apple TV Idle",
                    body=f"{_DEVICE_NAME} Apple TV has been on the home screen with nothing playing. Want to turn it off?",
                    tags=["tv", "eyes"],
                    priority=2,
                )
            except Exception as e:
                log.debug("ntfy push failed: %s", e)

    return {"status": "ok", "power": state["power"]}


def _parse_playing(raw: str) -> dict:
    """Parse atvremote 'playing' output into structured fields."""
    result: dict = {}
    for line in raw.splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        val = val.strip()
        if not val:
            continue
        if key == "title":
            result["title"] = val
        elif key == "artist":
            result["artist"] = val
        elif key == "album":
            result["album"] = val
        elif key == "media_type":
            result["media_type"] = val
        elif key == "position":
            result["position"] = val
        elif key == "total_time":
            result["total_time"] = val
    return result


# ---------------------------------------------------------------------------
# before_agent_start — inject TV state into system prompt
# ---------------------------------------------------------------------------
async def _before_agent_start(**kwargs):
    if not _tv_state:
        return None

    # Only inject if state is fresh (< 20 min old)
    updated = _tv_state.get("updated_at")
    if updated:
        try:
            updated_dt = datetime.fromisoformat(updated)
            age_minutes = (datetime.now() - updated_dt).total_seconds() / 60
            if age_minutes > 20:
                return None
        except (ValueError, TypeError):
            pass

    power = _tv_state.get("power", "unknown")
    if power == "off":
        inject = "\n\nApple TV (Living Room): Off\n"
    elif power == "unreachable":
        inject = "\n\nApple TV (Living Room): Unreachable\n"
    elif power == "on":
        parts = ["On"]
        title = _tv_state.get("title")
        app = _tv_state.get("app")
        position = _tv_state.get("position")
        total_time = _tv_state.get("total_time")

        if title:
            playing_str = f'Playing "{title}"'
            if app:
                playing_str += f" on {app}"
            if position and total_time:
                try:
                    pos_sec = _time_to_seconds(position)
                    total_sec = _time_to_seconds(total_time)
                    if total_sec > 0:
                        pct = int(pos_sec / total_sec * 100)
                        playing_str += f" ({pct}%)"
                except (ValueError, TypeError):
                    pass
            parts.append(playing_str)
        elif app:
            parts.append(f"App: {app}")

        device_st = _tv_state.get("device_state")
        if device_st and not title:
            parts.append(device_st)

        inject = f"\n\nApple TV (Living Room): {' — '.join(parts)}\n"
    else:
        return None

    system_prompt = kwargs.get("system_prompt", "")
    return {"system_prompt": system_prompt + inject}


def _time_to_seconds(time_str: str) -> float:
    """Convert a time string like '123.45' or '1:23:45' to seconds."""
    time_str = time_str.strip()
    if ":" in time_str:
        parts = time_str.split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
    return float(time_str)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
def register(api: PluginAPI):
    global _DEVICE_NAME, _ATVREMOTE_PATH, _TIMEOUT_SECONDS
    global _AUTO_OFF_HOUR, _IDLE_TIMEOUT_MINUTES, _AUTO_OFF_ENABLED

    # Apply config overrides if provided
    _DEVICE_NAME = api.config.get("device_name", _DEVICE_NAME)
    _ATVREMOTE_PATH = api.config.get("atvremote_path", _ATVREMOTE_PATH)
    _TIMEOUT_SECONDS = int(api.config.get("timeout_seconds", _TIMEOUT_SECONDS))
    _AUTO_OFF_HOUR = int(api.config.get("auto_off_hour", _AUTO_OFF_HOUR))
    _IDLE_TIMEOUT_MINUTES = int(api.config.get("idle_timeout_minutes", _IDLE_TIMEOUT_MINUTES))
    _AUTO_OFF_ENABLED = api.config.get("auto_off_enabled", _AUTO_OFF_ENABLED)

    api.register_tool(
        name="apple_tv",
        description=(
            "Control the Apple TV (Living Room). Send remote commands: "
            "navigation (up/down/left/right/select/menu/home), "
            "playback (play/pause/stop/next/previous/skip_forward/skip_backward), "
            "volume (volume_up/volume_down/set_volume), "
            "power (turn_on/turn_off/power_state), "
            "info (playing/title/app/device_state/position), "
            "apps (app_list/launch_app), "
            "text input (text_set/text_append/text_get/text_clear)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "The remote action to perform (e.g. 'play_pause', 'turn_off', 'playing').",
                },
                "value": {
                    "type": "string",
                    "description": "Optional value for actions that accept one (e.g. volume 0-100, app bundle_id, text to type, seconds to skip).",
                },
            },
            "required": ["action"],
        },
        handler=_apple_tv,
        permission="none",
    )

    api.register_hook("heartbeat_tick", _heartbeat_tick)
    api.register_hook("before_agent_start", _before_agent_start)

    api.log(f"Apple TV plugin registered — device: {_DEVICE_NAME}")
