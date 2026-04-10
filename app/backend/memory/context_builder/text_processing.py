"""Text processing, token estimation, and formatting helpers for context assembly.

Provides token counting heuristics, text normalization, truncation,
semantic overlap detection, date formatting, tier budget ratios,
and memory guidance generation. All functions are pure or nearly pure.
Consumed by runtime_helpers.py and runtime.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Callable
    from zoneinfo import ZoneInfo

    from backend.memory.context_builder.models import ContextEntry

from backend.ipc.models import extract_text_content as _extract_text
from backend.memory.context_builder.constants import (
    DISTILLATION_TOKEN_CAP_RATIO,
    LOW_SIGNAL_TOKENS,
    MAX_DIALOG_SUMMARY_SNIPPET_CHARS,
    MAX_RETRIEVED_CONTENT_CHARS,
    MEMORY_QUERY_DISTILLATION_TOKEN_CAP_RATIO,
    MEMORY_QUERY_MESSAGE_TOKEN_CAP_RATIO,
    MEMORY_QUERY_OBSERVATION_TOKEN_CAP_RATIO,
    MEMORY_QUERY_RETRIEVED_MSG_TOKEN_CAP_RATIO,
    MESSAGE_TOKEN_CAP_RATIO,
    OBSERVATION_TOKEN_CAP_RATIO,
    RETRIEVED_MSG_TOKEN_CAP_RATIO,
    TOKEN_RE,
)
from backend.memory.context_builder.runtime_models import COMPRESSED_MEMORY_GUIDANCE


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def estimate_tokens(text: str) -> int:
    """Estimate token count for a text string.

    Uses a simple heuristic of ~4 characters per token.
    This is approximate for Qwen3 models.

    Args:
        text: Text to estimate tokens for.

    Returns:
        Estimated token count.
    """
    return len(text) // 4


def should_compress(
    messages: list[tuple[str, str]],
    threshold: int,
) -> bool:
    """Check if messages exceed the token threshold.

    Args:
        messages: List of (role, content) tuples.
        threshold: Strict token threshold for compression. Required:
            the silent fallback default of 4000 has been removed.

    Returns:
        True if total tokens exceed threshold.
    """
    total = sum(estimate_tokens(content) for _, content in messages)
    return total > threshold


# ---------------------------------------------------------------------------
# Text processing helpers
# ---------------------------------------------------------------------------


def to_text(content: str | list) -> str:
    """Convert content to text, stripping images from multimodal."""
    if isinstance(content, str):
        return content
    return _extract_text(content)


def convert_utc_timestamp(utc_str: str, user_tz: ZoneInfo) -> str:
    """Convert a UTC timestamp string to the user's timezone.

    Args:
        utc_str: ISO 8601 timestamp without offset (UTC assumed).
        user_tz: Target timezone.

    Returns:
        Timestamp with seconds in the user's timezone,
        e.g. ``2026-03-10T14:30:45``.
    """
    try:
        dt = datetime.fromisoformat(utc_str).replace(tzinfo=UTC)
        return dt.astimezone(user_tz).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return utc_str[:19].replace("T", " ")


def semantic_tokens(text: str) -> set[str]:
    """Extract a compact set of informative tokens for overlap checks."""
    return {
        token
        for token in TOKEN_RE.findall(text.lower())
        if len(token) >= 4 and token not in LOW_SIGNAL_TOKENS
    }


def is_semantically_covered(
    candidate_text: str,
    semantic_texts: list[str],
) -> bool:
    """Check whether semantic memory already covers a retrieved message."""
    candidate_tokens = semantic_tokens(candidate_text)
    if len(candidate_tokens) < 2:
        return False

    for semantic_text in semantic_texts:
        sem_tokens = semantic_tokens(semantic_text)
        if len(sem_tokens) < 2:
            continue
        shared_tokens = candidate_tokens & sem_tokens
        if len(shared_tokens) < 2:
            continue
        overlap_ratio = len(shared_tokens) / len(candidate_tokens)
        if overlap_ratio >= 0.5:
            return True
    return False


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def normalize_structured_items(raw_items: object) -> list[str]:
    """Normalize structured memory fields into a clean string list."""
    if raw_items is None:
        return []
    if isinstance(raw_items, list):
        return [str(item).strip() for item in raw_items if str(item).strip()]
    if isinstance(raw_items, str):
        stripped = raw_items.strip()
        return [stripped] if stripped else []
    return []


def normalize_identifier_list(raw_identifiers: object) -> list[str]:
    """Normalize identifier collections into a clean string list."""
    if raw_identifiers is None:
        return []
    if isinstance(raw_identifiers, list):
        return [str(identifier) for identifier in raw_identifiers if str(identifier)]
    if isinstance(raw_identifiers, str):
        stripped = raw_identifiers.strip()
        return [stripped] if stripped else []
    return []


# ---------------------------------------------------------------------------
# Truncation helpers
# ---------------------------------------------------------------------------


def truncate_dialog_summary_snippet(content: str) -> str:
    """Trim message content for dialog-summary bullets."""
    normalized = " ".join(content.split())
    if len(normalized) <= MAX_DIALOG_SUMMARY_SNIPPET_CHARS:
        return normalized
    return normalized[: MAX_DIALOG_SUMMARY_SNIPPET_CHARS - 1].rstrip() + "…"


def truncate_retrieved_message_content(message_content: str) -> str:
    """Bound retrieved message content before prompt injection."""
    if len(message_content) <= MAX_RETRIEVED_CONTENT_CHARS:
        return message_content
    return message_content[:MAX_RETRIEVED_CONTENT_CHARS] + "… [truncated]"


# ---------------------------------------------------------------------------
# Date formatting
# ---------------------------------------------------------------------------


def build_date_formatter(
    user_tz: ZoneInfo | None,
) -> Callable[[str], str]:
    """Return one date-formatting helper bound to the active timezone."""

    def _fmt_date(raw: str) -> str:
        """Format a raw timestamp using the bound timezone."""
        if not raw:
            return ""
        if user_tz:
            return convert_utc_timestamp(raw, user_tz)
        return raw[:19].replace("T", " ")

    return _fmt_date


# ---------------------------------------------------------------------------
# Tier budget ratios
# ---------------------------------------------------------------------------


def tier_ratios(memory_query: bool) -> tuple[float, float, float, float]:
    """Return tier budget ratios for normal vs memory-seeking queries."""
    if memory_query:
        return (
            MEMORY_QUERY_MESSAGE_TOKEN_CAP_RATIO,
            MEMORY_QUERY_RETRIEVED_MSG_TOKEN_CAP_RATIO,
            MEMORY_QUERY_OBSERVATION_TOKEN_CAP_RATIO,
            MEMORY_QUERY_DISTILLATION_TOKEN_CAP_RATIO,
        )
    return (
        MESSAGE_TOKEN_CAP_RATIO,
        RETRIEVED_MSG_TOKEN_CAP_RATIO,
        OBSERVATION_TOKEN_CAP_RATIO,
        DISTILLATION_TOKEN_CAP_RATIO,
    )


# ---------------------------------------------------------------------------
# Memory guidance
# ---------------------------------------------------------------------------


def build_memory_guidance(
    *,
    entries: list[ContextEntry],
    summaries: list[str] | list[tuple[str, ...]],
) -> str | None:
    """Return concise system guidance when compressed memory is present."""
    if summaries:
        return COMPRESSED_MEMORY_GUIDANCE
    if any(entry.role == "dialog_summary" for entry in entries):
        return COMPRESSED_MEMORY_GUIDANCE
    return None
