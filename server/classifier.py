"""Intent classifier — heuristic + Haiku hybrid for smart routing."""

import logging
import re
from enum import Enum

from . import config

log = logging.getLogger("conduit.classifier")


class Intent(Enum):
    SIMPLE = "simple"           # Greetings, quick questions → NIM
    COMPLEX = "complex"         # Multi-step analysis → Opus
    LONG_CONTEXT = "long"       # Big input → Gemini
    REMINDER = "reminder"       # Natural language reminder
    COMMAND = "command"         # Explicit /command
    UNCERTAIN = "uncertain"    # Needs Haiku tiebreaker


# Greeting patterns
_GREETINGS = re.compile(
    r"^(hi|hey|hello|yo|sup|what'?s up|good morning|good afternoon|good evening|gm|howdy)\b",
    re.IGNORECASE,
)

# Simple question patterns
_SIMPLE_PATTERNS = [
    re.compile(r"^(what|who|when|where|how)\s+(is|are|was|were|do|does|did|can|would)\b", re.IGNORECASE),
    re.compile(r"^(tell me|explain|define|describe)\s", re.IGNORECASE),
]

# Complexity markers
_COMPLEXITY_MARKERS = [
    (re.compile(r"\b(analyze|compare|contrast|evaluate|assess)\b", re.IGNORECASE), 15),
    (re.compile(r"\b(trade-?offs?|pros?\s+(?:and|&)\s+cons?|advantages?\s+(?:and|&)\s+disadvantages?)\b", re.IGNORECASE), 20),
    (re.compile(r"\b(implement|architect|design|refactor|optimize)\b", re.IGNORECASE), 15),
    (re.compile(r"\b(step[\s-]by[\s-]step|multi[\s-]step|complex)\b", re.IGNORECASE), 10),
    (re.compile(r"```", re.IGNORECASE), 15),  # Code blocks
    (re.compile(r"\b(debug|fix|error|bug|issue|problem)\b", re.IGNORECASE), 10),
    (re.compile(r"\b(strategy|plan|roadmap|architecture)\b", re.IGNORECASE), 15),
]

# Natural language reminder patterns
_REMINDER_PATTERNS = [
    re.compile(r"\bremind\s+me\b", re.IGNORECASE),
    re.compile(r"\bdon'?t\s+(let\s+me\s+)?forget\b", re.IGNORECASE),
    re.compile(r"\bset\s+a\s+reminder\b", re.IGNORECASE),
    re.compile(r"\bremember\s+to\b", re.IGNORECASE),
]

# Multi-question detection
_QUESTION_MARK = re.compile(r"\?")
_NUMBERED_LIST = re.compile(r"^\s*\d+[\.\)]\s", re.MULTILINE)


def _score_complexity(content: str, conversation_length: int = 0) -> int:
    """Score message complexity 0-100 using heuristics."""
    score = 0

    # Length contributes
    char_len = len(content)
    if char_len > 500:
        score += 10
    if char_len > 1000:
        score += 10
    if char_len > 2000:
        score += 15

    # Keyword density
    for pattern, weight in _COMPLEXITY_MARKERS:
        if pattern.search(content):
            score += weight

    # Multiple questions
    q_count = len(_QUESTION_MARK.findall(content))
    if q_count >= 3:
        score += 15
    elif q_count >= 2:
        score += 8

    # Numbered/bulleted lists (suggests multi-part request)
    if _NUMBERED_LIST.search(content):
        score += 10

    # Long conversation context adds complexity
    if conversation_length > 20:
        score += 5

    return min(score, 100)


def classify_fast(content: str, conversation_length: int = 0) -> tuple[Intent, str | None]:
    """Fast heuristic classification. Returns (intent, recommended_provider).

    Provider is None for default (NIM).
    """
    # Commands
    if content.startswith("/"):
        first_word = content.split()[0].lower()
        if first_word in {"/opus", "/think"}:
            return Intent.COMMAND, config.ESCALATION_PROVIDER
        if first_word in {"/research", "/gemini"}:
            return Intent.COMMAND, config.LONG_CONTEXT_PROVIDER
        if first_word in {"/code"}:
            return Intent.COMMAND, "claude_code"
        if first_word in {"/or", "/openrouter"}:
            return Intent.COMMAND, "openrouter"
        return Intent.COMMAND, None

    # Greetings
    if _GREETINGS.match(content) and len(content) < 50:
        return Intent.SIMPLE, None

    # Natural language reminders
    for pattern in _REMINDER_PATTERNS:
        if pattern.search(content):
            return Intent.REMINDER, None

    # Long context
    if len(content) > config.LONG_CONTEXT_CHARS:
        if config.LONG_CONTEXT_PROVIDER:
            return Intent.LONG_CONTEXT, config.LONG_CONTEXT_PROVIDER
        return Intent.SIMPLE, None

    # Complexity scoring
    score = _score_complexity(content, conversation_length)

    low, high = config.HAIKU_BAND
    if score >= high:
        return Intent.COMPLEX, config.ESCALATION_PROVIDER
    if score <= low:
        return Intent.SIMPLE, None

    # Uncertain — in the band, needs Haiku tiebreaker
    return Intent.UNCERTAIN, None


async def classify_with_haiku(content: str, providers: dict) -> tuple[Intent, str | None]:
    """Use Haiku for uncertain cases. Cheap (~50 tokens)."""
    brain = providers.get(config.BRAIN_PROVIDER)
    if not brain:
        # No Haiku available — default to simple
        return Intent.SIMPLE, None

    try:
        prompt = (
            "Classify this user message as SIMPLE or COMPLEX.\n"
            "SIMPLE = casual chat, quick questions, greetings, opinions, recommendations.\n"
            "COMPLEX = multi-step analysis, code review, architecture decisions, deep reasoning.\n\n"
            f"Message: {content[:500]}\n\n"
            "Reply with exactly one word: SIMPLE or COMPLEX"
        )
        response, usage = await brain.generate(
            [{"role": "user", "content": prompt}],
            system="You are a message classifier. Reply with exactly one word.",
        )

        # Log the haiku usage
        from . import db
        await db.log_usage(brain.name, brain.model, usage.input_tokens, usage.output_tokens)

        result = response.strip().upper()
        log.info("Haiku classification: %s (tokens: %d in, %d out)",
                 result, usage.input_tokens, usage.output_tokens)

        if "COMPLEX" in result:
            return Intent.COMPLEX, config.ESCALATION_PROVIDER
        return Intent.SIMPLE, None

    except Exception as e:
        log.warning("Haiku classification failed: %s — defaulting to SIMPLE", e)
        return Intent.SIMPLE, None


async def classify(content: str, providers: dict, conversation_length: int = 0) -> tuple[Intent, str | None]:
    """Full classification pipeline: heuristics first, Haiku for tiebreaker.

    Returns (intent, recommended_provider_name).
    """
    intent, provider = classify_fast(content, conversation_length)

    if intent == Intent.UNCERTAIN:
        log.info("Heuristic uncertain (score in band) — asking Haiku")
        intent, provider = await classify_with_haiku(content, providers)

    log.info("Classified as %s → provider: %s", intent.value, provider or "default")
    return intent, provider
