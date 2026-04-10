"""Zone-based context builder for LLM prompt assembly.

Assembles LLM context within strict zone budgets that auto-scale
with the model's context window. Three zones:
1. Working memory (55%) — recent conversation messages, rolling buffer
2. Recall results (25%) — memory_recall tool output, current turn only
3. Conversation summary (20%) — compressed history from evicted messages

Unlike the legacy 4-tier builder, this module does NOT load records
or summaries automatically. The agent retrieves memories on demand
via the memory_recall tool.
"""

from __future__ import annotations

import logging

from .constants import estimate_tokens
from .models import ContextEntry, ContextResult, ContextZoneBudget


logger = logging.getLogger(__name__)


def build_zone_context(
    *,
    recent_messages: list[tuple[str, str]],
    conversation_summary: str = "",
    recall_results: str = "",
    zone_budget: ContextZoneBudget,
) -> ContextResult:
    """Assemble LLM context within zone budgets.

    Args:
        recent_messages: List of (role, content) tuples, newest last.
        conversation_summary: Compressed history of evicted messages.
        recall_results: Formatted output from memory_recall tool.
        zone_budget: Computed zone token caps.

    Returns:
        ContextResult with zone-budgeted entries.
    """
    entries: list[ContextEntry] = []

    message_entries, evicted = _build_message_entries(
        recent_messages,
        zone_budget.working_memory,
        zone_budget.eviction_ratio,
    )

    summary_entry = _build_summary_entry(
        conversation_summary,
        zone_budget.conversation_summary,
    )
    if summary_entry is not None:
        entries.append(summary_entry)

    entries.extend(message_entries)

    if recall_results:
        recall_entry = _build_recall_entry(
            recall_results,
            zone_budget.recall_results,
        )
        if recall_entry is not None:
            entries.append(recall_entry)

    working_used = sum(estimate_tokens(e.content) for e in message_entries)
    summary_used = estimate_tokens(summary_entry.content) if summary_entry else 0
    recall_used = sum(estimate_tokens(e.content) for e in entries if e.role == "recall")
    total_used = working_used + summary_used + recall_used
    logger.info(
        "Zone context: %d entries, ~%d tokens (budget: %d)",
        len(entries),
        total_used,
        zone_budget.total_available,
    )

    return ContextResult(
        entries=entries,
        retrieved_memories=[],
        zone_usage={
            "working_memory": working_used,
            "recall_results": recall_used,
            "conversation_summary": summary_used,
        },
        evicted_messages=evicted if evicted else None,
    )


def evict_oldest_messages(
    messages: list[tuple[str, str]],
    budget_tokens: int,
    eviction_ratio: float = 0.0,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Split messages into kept and evicted based on token budget.

    When eviction_ratio > 0 and the total exceeds budget, keeps only
    the newest messages that fit within ``budget * (1 - eviction_ratio)``
    so that the working zone has breathing room for several turns.

    Args:
        messages: List of (role, content) tuples, newest last.
        budget_tokens: Maximum tokens for kept messages.
        eviction_ratio: Fraction of budget to free on overflow (0.0-1.0).

    Returns:
        Tuple of (kept_messages, evicted_messages).
    """
    if not messages:
        return [], []

    total_cost = sum(estimate_tokens(m[1]) for m in messages)
    if total_cost <= budget_tokens:
        return list(messages), []

    keep_budget = int(budget_tokens * (1.0 - eviction_ratio))

    total = 0
    keep_from = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        cost = estimate_tokens(messages[i][1])
        if total + cost > keep_budget:
            break
        total += cost
        keep_from = i

    return messages[keep_from:], messages[:keep_from]


def compress_messages_to_summary(
    messages: list[tuple[str, str]],
) -> str:
    """Compress a block of messages into a one-line summary.

    Uses a simple extractive approach: takes the first user message
    content and first assistant response as representative.

    Args:
        messages: List of (role, content) tuples to compress.

    Returns:
        One-line summary string.
    """
    if not messages:
        return ""
    user_parts: list[str] = []
    assistant_parts: list[str] = []
    for role, content in messages:
        snippet = content[:100].replace("\n", " ").strip()
        if role == "user" and len(user_parts) < 2:
            user_parts.append(snippet)
        elif role == "assistant" and len(assistant_parts) < 2:
            assistant_parts.append(snippet)
    parts = []
    if user_parts:
        parts.append(f"User asked: {'; '.join(user_parts)}")
    if assistant_parts:
        parts.append(f"Assistant: {'; '.join(assistant_parts)}")
    return "[Earlier] " + ". ".join(parts) if parts else ""


def _format_messages_for_compression(
    messages: list[tuple[str, str]],
) -> str:
    """Format evicted messages as text for the LLM compression prompt."""
    lines: list[str] = []
    for role, content in messages:
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _response_text(response: object) -> str:
    """Extract text content from one compression LLM response object."""
    if hasattr(response, "text"):
        return str(response.text).strip()
    if hasattr(response, "content"):
        return str(response.content).strip()
    return ""


def _fallback_summary(
    messages: list[tuple[str, str]],
    existing_summary: str,
) -> str:
    """Build the primitive fallback summary, preserving existing state."""
    new_part = compress_messages_to_summary(messages)
    if not new_part:
        return existing_summary
    if not existing_summary.strip():
        return new_part
    return f"{existing_summary}\n{new_part}"


async def compress_evicted_with_llm(
    messages: list[tuple[str, str]],
    existing_summary: str,
    compression_llm_call: object,
) -> str:
    """Compress evicted messages using the dialog recorder LLM model.

    Produces a structured summary (Goal/Progress/Decisions/Resources/Next).
    When an existing summary is provided, instructs the model to update
    it rather than regenerate from scratch.

    Falls back to primitive extractive compression on any error.

    Args:
        messages: Evicted (role, content) tuples to compress.
        existing_summary: Previous S-zone content (empty for first eviction).
        compression_llm_call: Async callable(messages) -> response with .text.

    Returns:
        Structured summary string.
    """
    from .constants import (
        S_ZONE_COMPRESSION_MAX_TOKENS,
        S_ZONE_COMPRESSION_SYSTEM_PROMPT,
        S_ZONE_UPDATE_INSTRUCTION,
    )

    if not messages:
        return existing_summary

    conversation_text = _format_messages_for_compression(messages)

    user_content = conversation_text
    if existing_summary.strip():
        user_content = (
            f"{S_ZONE_UPDATE_INSTRUCTION}{existing_summary}\n\n"
            f"New conversation segment:\n{conversation_text}"
        )

    prompt_messages = [
        {"role": "system", "content": S_ZONE_COMPRESSION_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        response = await compression_llm_call(
            prompt_messages,
            max_tokens=S_ZONE_COMPRESSION_MAX_TOKENS,
        )
        result = _response_text(response)
        if result:
            return result
    except Exception:
        logger.warning(
            "S-zone LLM compression failed, using primitive fallback",
            exc_info=True,
        )

    return _fallback_summary(messages, existing_summary)


def _build_summary_entry(
    summary: str,
    budget_tokens: int,
) -> ContextEntry | None:
    """Build one conversation summary entry within budget."""
    if not summary.strip():
        return None
    cost = estimate_tokens(summary)
    if cost > budget_tokens:
        truncated = summary[: budget_tokens * 4]
        return ContextEntry(role="summary", content=truncated)
    return ContextEntry(role="summary", content=summary)


def _build_message_entries(
    messages: list[tuple[str, str]],
    budget_tokens: int,
    eviction_ratio: float = 0.0,
) -> tuple[list[ContextEntry], list[tuple[str, str]]]:
    """Build message entries from newest, within budget.

    Args:
        messages: List of (role, content) tuples, newest last.
        budget_tokens: Maximum tokens for kept messages.
        eviction_ratio: Fraction of budget to free on overflow.

    Returns:
        Tuple of (kept entries, evicted message tuples).
    """
    kept, evicted = evict_oldest_messages(
        messages,
        budget_tokens,
        eviction_ratio,
    )
    entries = [ContextEntry(role=role, content=content) for role, content in kept]
    return entries, evicted


def _build_recall_entry(
    recall_results: str,
    budget_tokens: int,
) -> ContextEntry | None:
    """Build one recall results entry within budget."""
    if not recall_results.strip():
        return None
    cost = estimate_tokens(recall_results)
    if cost > budget_tokens:
        truncated = recall_results[: budget_tokens * 4]
        return ContextEntry(
            role="recall",
            content=truncated + "\n[recall truncated]",
        )
    return ContextEntry(role="recall", content=recall_results)
