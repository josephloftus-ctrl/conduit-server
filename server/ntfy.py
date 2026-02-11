"""ntfy push notification client — sends to self-hosted ntfy server."""

import logging

import httpx

from . import config

log = logging.getLogger("conduit.ntfy")


async def push(
    title: str,
    body: str,
    tags: list[str] | None = None,
    priority: int = 3,
    click_url: str | None = None,
):
    """Send a push notification via ntfy.

    Args:
        title: Notification title.
        body: Notification body text.
        tags: Optional ntfy tags (emoji shortcodes).
        priority: 1-5, default 3 (normal).
        click_url: URL to open when notification is tapped.
    """
    if not config.NTFY_ENABLED:
        return

    server = config.NTFY_SERVER
    topic = config.NTFY_TOPIC
    token = config.NTFY_TOKEN

    if not server or not topic:
        log.warning("ntfy not configured (missing server or topic)")
        return

    url = f"{server.rstrip('/')}/{topic}"

    headers = {
        "Title": title,
        "Priority": str(priority),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if tags:
        headers["Tags"] = ",".join(tags)
    if click_url:
        headers["Click"] = click_url

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, content=body, headers=headers)
            if resp.status_code == 200:
                log.info("ntfy push sent: %s", title)
            else:
                log.warning("ntfy push failed (%d): %s", resp.status_code, resp.text)
    except Exception as e:
        log.error("ntfy push error: %s", e)
