"""Email tools — read Outlook inbox via Microsoft Graph."""

import logging
import re

from . import register
from .definitions import ToolDefinition
from .. import outlook

log = logging.getLogger("conduit.tools.email")

MAX_BODY_SIZE = 20 * 1024  # 20KB truncation limit


def _format_sender(msg: dict) -> str:
    """Extract sender display string from Graph message."""
    fr = msg.get("from", {}).get("emailAddress", {})
    name = fr.get("name", "")
    addr = fr.get("address", "")
    return f"{name} <{addr}>" if name else addr


def _format_date(msg: dict) -> str:
    """Format receivedDateTime to readable string."""
    raw = msg.get("receivedDateTime", "")
    # Graph returns ISO 8601: 2024-01-15T14:30:00Z
    return raw.replace("T", " ").replace("Z", " UTC").strip() if raw else "unknown"


def _strip_html(html: str) -> str:
    """Basic HTML tag removal for email bodies."""
    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


async def _read_inbox(count: int = 10, unread_only: bool = False) -> str:
    """Read recent inbox messages."""
    if not outlook.is_configured():
        return "Error: Outlook is not configured. Set OUTLOOK_CLIENT_ID and run the auth setup."

    token = outlook.get_access_token()
    if not token:
        return "Error: Outlook authentication expired. Run `python -m server.setup_outlook_auth` to re-authenticate."

    messages = await outlook.get_inbox(count=count, unread_only=unread_only)
    if not messages:
        label = "unread " if unread_only else ""
        return f"No {label}messages in inbox."

    lines = [f"Inbox ({len(messages)} messages):\n"]
    for i, msg in enumerate(messages, 1):
        sender = _format_sender(msg)
        subject = msg.get("subject", "(no subject)")
        date = _format_date(msg)
        preview = msg.get("bodyPreview", "")[:120]
        unread = " [UNREAD]" if not msg.get("isRead", True) else ""
        msg_id = msg.get("id", "")

        lines.append(f"{i}. {subject}{unread}")
        lines.append(f"   From: {sender}")
        lines.append(f"   Date: {date}")
        if preview:
            lines.append(f"   Preview: {preview}")
        lines.append(f"   ID: {msg_id}")
        lines.append("")

    return "\n".join(lines).strip()


async def _search_email(query: str, count: int = 10) -> str:
    """Search email messages."""
    if not outlook.is_configured():
        return "Error: Outlook is not configured. Set OUTLOOK_CLIENT_ID and run the auth setup."

    token = outlook.get_access_token()
    if not token:
        return "Error: Outlook authentication expired. Run `python -m server.setup_outlook_auth` to re-authenticate."

    messages = await outlook.search_messages(query=query, count=count)
    if not messages:
        return f"No messages found matching: {query}"

    lines = [f"Search results for '{query}' ({len(messages)} messages):\n"]
    for i, msg in enumerate(messages, 1):
        sender = _format_sender(msg)
        subject = msg.get("subject", "(no subject)")
        date = _format_date(msg)
        preview = msg.get("bodyPreview", "")[:120]
        msg_id = msg.get("id", "")

        lines.append(f"{i}. {subject}")
        lines.append(f"   From: {sender}")
        lines.append(f"   Date: {date}")
        if preview:
            lines.append(f"   Preview: {preview}")
        lines.append(f"   ID: {msg_id}")
        lines.append("")

    return "\n".join(lines).strip()


async def _read_email(message_id: str) -> str:
    """Read a single email message by ID."""
    if not outlook.is_configured():
        return "Error: Outlook is not configured. Set OUTLOOK_CLIENT_ID and run the auth setup."

    token = outlook.get_access_token()
    if not token:
        return "Error: Outlook authentication expired. Run `python -m server.setup_outlook_auth` to re-authenticate."

    msg = await outlook.get_message(message_id)
    if not msg:
        return f"Error: Could not retrieve message {message_id}"

    subject = msg.get("subject", "(no subject)")
    sender = _format_sender(msg)
    date = _format_date(msg)

    # Recipients
    to_list = msg.get("toRecipients", [])
    to_str = ", ".join(
        r.get("emailAddress", {}).get("address", "") for r in to_list
    )

    # Body
    body_obj = msg.get("body", {})
    body_type = body_obj.get("contentType", "text")
    body_content = body_obj.get("content", "")

    if body_type == "html":
        body_content = _strip_html(body_content)

    if len(body_content) > MAX_BODY_SIZE:
        body_content = body_content[:MAX_BODY_SIZE] + "\n\n... [truncated at 20KB]"

    lines = [
        f"Subject: {subject}",
        f"From: {sender}",
        f"To: {to_str}",
        f"Date: {date}",
        "",
        body_content,
    ]
    return "\n".join(lines)


def register_all():
    """Register email tools."""
    register(ToolDefinition(
        name="read_inbox",
        description="Read recent messages from the user's Outlook inbox. Returns sender, subject, date, preview, and message ID.",
        parameters={
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "Number of messages to return (default 10, max 25)",
                },
                "unread_only": {
                    "type": "boolean",
                    "description": "If true, only return unread messages",
                },
            },
            "required": [],
        },
        handler=_read_inbox,
        permission="none",
    ))

    register(ToolDefinition(
        name="search_email",
        description="Search the user's Outlook email by keyword query. Returns matching messages with sender, subject, date, and preview.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (e.g. 'from:amazon receipt', 'meeting agenda')",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of results to return (default 10)",
                },
            },
            "required": ["query"],
        },
        handler=_search_email,
        permission="none",
    ))

    register(ToolDefinition(
        name="read_email",
        description="Read the full content of a specific email by its message ID. Use read_inbox or search_email first to get the ID.",
        parameters={
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "The message ID from read_inbox or search_email results",
                },
            },
            "required": ["message_id"],
        },
        handler=_read_email,
        permission="none",
    ))
