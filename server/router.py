"""Model router — thin wrapper around classifier with budget gating."""

import logging

from . import config, db
from .classifier import Intent, classify

log = logging.getLogger("conduit.router")


async def route(content: str, providers: dict, conversation_length: int = 0) -> str | None:
    """Determine which provider should handle this message.

    Returns provider name or None for default.
    """
    intent, provider_name = await classify(content, providers, conversation_length)

    # Budget-gate Opus
    if provider_name == config.ESCALATION_PROVIDER:
        actual = await _try_opus(providers)
        if actual != config.ESCALATION_PROVIDER:
            log.info("Opus budget exceeded — falling back to %s", actual)
        return actual

    # Check provider availability
    if provider_name and provider_name not in providers:
        log.warning("Recommended provider %s not available — using default", provider_name)
        return None

    return provider_name


async def _try_opus(providers: dict) -> str | None:
    """Try to use Opus, checking budget first. Falls back to Gemini, then default."""
    if config.ESCALATION_PROVIDER not in providers:
        log.warning("Opus requested but not configured")
        if config.LONG_CONTEXT_PROVIDER in providers:
            return config.LONG_CONTEXT_PROVIDER
        return None

    used = await db.get_daily_opus_tokens()
    if used >= config.OPUS_DAILY_BUDGET:
        log.warning("Opus budget exhausted (%d/%d tokens)", used, config.OPUS_DAILY_BUDGET)
        if config.LONG_CONTEXT_PROVIDER in providers:
            return config.LONG_CONTEXT_PROVIDER
        return None

    return config.ESCALATION_PROVIDER


def strip_command(content: str) -> str:
    """Remove the /command prefix from user content before sending to model."""
    if content and content[0] == "/":
        parts = content.split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ""
    return content
