"""Google Calendar tools — list, create, update, delete events via REST API."""

import logging
import os
import time
from datetime import datetime, timedelta, timezone

import httpx

from . import register
from .definitions import ToolDefinition

log = logging.getLogger("conduit.tools.calendar")

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3"

# In-memory token cache
_token_cache: dict[str, str | float] = {
    "access_token": "",
    "expires_at": 0.0,
}


def _is_configured() -> bool:
    """Check if Google Calendar credentials are available."""
    return bool(
        os.getenv("GOOGLE_CLIENT_ID")
        and os.getenv("GOOGLE_CLIENT_SECRET")
        and os.getenv("GOOGLE_REFRESH_TOKEN")
    )


async def _get_access_token() -> str:
    """Get a valid access token, refreshing if expired."""
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]

    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
    refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN", "")

    if not all([client_id, client_secret, refresh_token]):
        raise RuntimeError("Google Calendar credentials not configured in .env")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"] = time.time() + data.get("expires_in", 3600)
    return data["access_token"]


async def _api_request(method: str, path: str, **kwargs) -> dict | list | None:
    """Make an authenticated request to the Google Calendar API. Auto-retries on 401."""
    token = await _get_access_token()
    url = f"{GOOGLE_CALENDAR_API}{path}"
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient() as client:
        resp = await client.request(method, url, headers=headers, **kwargs)

        # Retry once on 401 (expired token)
        if resp.status_code == 401:
            _token_cache["access_token"] = ""
            token = await _get_access_token()
            headers["Authorization"] = f"Bearer {token}"
            resp = await client.request(method, url, headers=headers, **kwargs)

        if resp.status_code == 204:
            return None
        resp.raise_for_status()
        return resp.json()


def _get_calendar_id() -> str:
    return os.getenv("GOOGLE_CALENDAR_ID", "primary")


def _parse_datetime(dt_str: str) -> str:
    """Parse a datetime string to ISO format. Handles ISO format and natural language basics."""
    # Already ISO format
    if "T" in dt_str:
        # Ensure timezone
        if not dt_str.endswith("Z") and "+" not in dt_str and "-" not in dt_str[10:]:
            return dt_str + "-05:00"  # Default to Eastern
        return dt_str

    # Try natural language patterns
    now = datetime.now()
    lower = dt_str.lower().strip()

    # "tomorrow 2pm", "tomorrow at 2pm"
    if lower.startswith("tomorrow"):
        day = now + timedelta(days=1)
        time_part = lower.replace("tomorrow", "").replace("at", "").strip()
        hour = _parse_time(time_part) if time_part else 9
        return day.replace(hour=hour, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S") + "-05:00"

    # "today 3pm"
    if lower.startswith("today"):
        time_part = lower.replace("today", "").replace("at", "").strip()
        hour = _parse_time(time_part) if time_part else 9
        return now.replace(hour=hour, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%S") + "-05:00"

    # Try parsing as date/time directly
    for fmt in ["%Y-%m-%d %I:%M%p", "%Y-%m-%d %H:%M", "%Y-%m-%d %I%p", "%Y-%m-%d"]:
        try:
            parsed = datetime.strptime(dt_str, fmt)
            return parsed.strftime("%Y-%m-%dT%H:%M:%S") + "-05:00"
        except ValueError:
            continue

    # Fallback: return as-is (let Google API handle it)
    return dt_str


def _parse_time(time_str: str) -> int:
    """Parse a simple time string like '2pm', '14:00', '3:30pm' to hour (24h)."""
    s = time_str.lower().strip().replace(" ", "")
    try:
        if ":" in s:
            if "pm" in s:
                parts = s.replace("pm", "").split(":")
                h = int(parts[0])
                return h + 12 if h < 12 else h
            elif "am" in s:
                parts = s.replace("am", "").split(":")
                h = int(parts[0])
                return h if h < 12 else 0
            else:
                return int(s.split(":")[0])
        elif "pm" in s:
            h = int(s.replace("pm", ""))
            return h + 12 if h < 12 else h
        elif "am" in s:
            h = int(s.replace("am", ""))
            return h if h < 12 else 0
        else:
            return int(s)
    except (ValueError, IndexError):
        return 9  # Default to 9am


def _format_event(event: dict) -> str:
    """Format a calendar event for display."""
    title = event.get("summary", "(No title)")
    location = event.get("location", "")
    description = event.get("description", "")
    event_id = event.get("id", "")
    html_link = event.get("htmlLink", "")

    # Parse start/end times
    start = event.get("start", {})
    end = event.get("end", {})

    if "dateTime" in start:
        start_dt = datetime.fromisoformat(start["dateTime"])
        end_dt = datetime.fromisoformat(end.get("dateTime", start["dateTime"]))
        date_str = start_dt.strftime("%a %b %d")
        time_str = f"{start_dt.strftime('%I:%M%p').lstrip('0')} – {end_dt.strftime('%I:%M%p').lstrip('0')}"
    elif "date" in start:
        date_str = start["date"]
        time_str = "All day"
    else:
        date_str = "Unknown date"
        time_str = ""

    lines = [f"  {date_str}  {time_str}  {title}"]
    if location:
        lines.append(f"    Location: {location}")
    if description:
        snippet = description[:120].replace("\n", " ")
        lines.append(f"    Note: {snippet}")
    lines.append(f"    ID: {event_id}")
    if html_link:
        lines.append(f"    Link: {html_link}")
    return "\n".join(lines)


async def _list_calendar_events(
    days_ahead: int = 7,
    max_results: int = 10,
    query: str = "",
) -> str:
    """List upcoming calendar events."""
    if not _is_configured():
        return "Error: Google Calendar not configured. Add GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REFRESH_TOKEN to server/.env"

    try:
        cal_id = _get_calendar_id()
        now = datetime.now(timezone.utc)
        time_max = now + timedelta(days=days_ahead)

        params = {
            "timeMin": now.isoformat(),
            "timeMax": time_max.isoformat(),
            "maxResults": max_results,
            "singleEvents": "true",
            "orderBy": "startTime",
        }
        if query:
            params["q"] = query

        data = await _api_request("GET", f"/calendars/{cal_id}/events", params=params)
        events = data.get("items", []) if data else []

        if not events:
            q_label = f" matching '{query}'" if query else ""
            return f"No events{q_label} in the next {days_ahead} day(s)."

        lines = [f"Calendar — next {days_ahead} day(s):\n"]
        for event in events:
            lines.append(_format_event(event))
            lines.append("")

        return "\n".join(lines).strip()

    except httpx.HTTPStatusError as e:
        return f"Google Calendar API error: {e.response.status_code} — {e.response.text[:200]}"
    except Exception as e:
        return f"Error accessing Google Calendar: {e}"


async def _create_calendar_event(
    title: str,
    start: str,
    end: str = "",
    description: str = "",
    location: str = "",
) -> str:
    """Create a new calendar event."""
    if not _is_configured():
        return "Error: Google Calendar not configured. Add credentials to server/.env"

    try:
        cal_id = _get_calendar_id()
        start_iso = _parse_datetime(start)

        if not end:
            # Default to 1 hour duration
            try:
                start_dt = datetime.fromisoformat(start_iso)
                end_dt = start_dt + timedelta(hours=1)
                end_iso = end_dt.isoformat()
            except ValueError:
                end_iso = start_iso
        else:
            end_iso = _parse_datetime(end)

        body = {
            "summary": title,
            "start": {"dateTime": start_iso, "timeZone": "America/New_York"},
            "end": {"dateTime": end_iso, "timeZone": "America/New_York"},
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location

        event = await _api_request("POST", f"/calendars/{cal_id}/events", json=body)

        link = event.get("htmlLink", "") if event else ""
        link_note = f"\nLink: {link}" if link else ""
        loc_note = f"\nLocation: {location}" if location else ""

        return (
            f"Event created: {title}\n"
            f"When: {start_iso} → {end_iso}{loc_note}{link_note}"
        )

    except httpx.HTTPStatusError as e:
        return f"Google Calendar API error: {e.response.status_code} — {e.response.text[:200]}"
    except Exception as e:
        return f"Error creating event: {e}"


async def _update_calendar_event(
    event_id: str,
    title: str = "",
    start: str = "",
    end: str = "",
    description: str = "",
    location: str = "",
) -> str:
    """Update an existing calendar event. Only provided fields are changed."""
    if not _is_configured():
        return "Error: Google Calendar not configured."

    try:
        cal_id = _get_calendar_id()

        body: dict = {}
        if title:
            body["summary"] = title
        if start:
            body["start"] = {"dateTime": _parse_datetime(start), "timeZone": "America/New_York"}
        if end:
            body["end"] = {"dateTime": _parse_datetime(end), "timeZone": "America/New_York"}
        if description:
            body["description"] = description
        if location:
            body["location"] = location

        if not body:
            return "No fields to update. Provide at least one of: title, start, end, description, location."

        event = await _api_request("PATCH", f"/calendars/{cal_id}/events/{event_id}", json=body)

        updated_title = event.get("summary", title) if event else title
        return f"Updated event: {updated_title} (ID: {event_id})"

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Event not found: {event_id}"
        return f"Google Calendar API error: {e.response.status_code} — {e.response.text[:200]}"
    except Exception as e:
        return f"Error updating event: {e}"


async def _delete_calendar_event(event_id: str) -> str:
    """Delete a calendar event."""
    if not _is_configured():
        return "Error: Google Calendar not configured."

    try:
        cal_id = _get_calendar_id()
        await _api_request("DELETE", f"/calendars/{cal_id}/events/{event_id}")
        return f"Event deleted (ID: {event_id})"

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Event not found: {event_id}"
        return f"Google Calendar API error: {e.response.status_code} — {e.response.text[:200]}"
    except Exception as e:
        return f"Error deleting event: {e}"


def register_all():
    """Register Google Calendar tools."""
    register(ToolDefinition(
        name="list_calendar_events",
        description=(
            "List upcoming events from the user's Google Calendar. "
            "Defaults to next 7 days. Can search by keyword."
        ),
        parameters={
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "Number of days to look ahead (default: 7)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum events to return (default: 10)",
                },
                "query": {
                    "type": "string",
                    "description": "Search query to filter events by title/description",
                },
            },
            "required": [],
        },
        handler=_list_calendar_events,
        permission="none",
    ))

    register(ToolDefinition(
        name="create_calendar_event",
        description=(
            "Create a new event on the user's Google Calendar. "
            "If end time is omitted, defaults to 1 hour duration."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Event title",
                },
                "start": {
                    "type": "string",
                    "description": "Start time (ISO datetime like '2026-02-12T14:00:00' or natural like 'tomorrow 2pm')",
                },
                "end": {
                    "type": "string",
                    "description": "End time (same formats as start). Defaults to 1 hour after start.",
                },
                "description": {
                    "type": "string",
                    "description": "Event description/notes",
                },
                "location": {
                    "type": "string",
                    "description": "Event location",
                },
            },
            "required": ["title", "start"],
        },
        handler=_create_calendar_event,
        permission="write",
    ))

    register(ToolDefinition(
        name="update_calendar_event",
        description=(
            "Update an existing calendar event. Only the provided fields are changed. "
            "Use list_calendar_events first to get the event ID."
        ),
        parameters={
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "Event ID from list_calendar_events",
                },
                "title": {
                    "type": "string",
                    "description": "New event title",
                },
                "start": {
                    "type": "string",
                    "description": "New start time",
                },
                "end": {
                    "type": "string",
                    "description": "New end time",
                },
                "description": {
                    "type": "string",
                    "description": "New description",
                },
                "location": {
                    "type": "string",
                    "description": "New location",
                },
            },
            "required": ["event_id"],
        },
        handler=_update_calendar_event,
        permission="write",
    ))

    register(ToolDefinition(
        name="delete_calendar_event",
        description=(
            "Delete an event from the user's Google Calendar. "
            "Use list_calendar_events first to find the event ID."
        ),
        parameters={
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "Event ID to delete",
                },
            },
            "required": ["event_id"],
        },
        handler=_delete_calendar_event,
        permission="write",
    ))
