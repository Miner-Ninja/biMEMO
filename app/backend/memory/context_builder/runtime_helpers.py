"""Stateless helper functions for the four-tier context assembly pipeline.

Provides dialog compression, compact message selection, and prompt entry
building helpers. Re-exports text processing, ranking, and metadata
functions from dedicated submodules. All functions are pure or nearly pure
and do not directly assemble the final context. Consumed by runtime.py.
"""

from __future__ import annotations

import logging

from backend.memory.context_builder.constants import (
    AUTO_COMPRESSION_MIN_TOKEN_BUDGET,
    AUTO_COMPRESSION_TRIGGER_RATIO,
    COMPRESSED_MESSAGE_CAP_RATIO,
    MAX_DIALOG_SUMMARY_SNIPPETS,
    MIN_RAW_MESSAGES_TO_KEEP,
)
from backend.memory.context_builder.models import ContextEntry
from backend.memory.context_builder.ranking_helpers import (
    build_record_content,
    build_record_prompt_entry,
    build_retrieved_message_text,
    build_semantic_coverage_texts,
    build_summary_prompt_entry,
    compute_suppressed_record_ids,
    is_suppressed_record,
    rank_observations_by_id,
    rank_reflections_by_id,
    should_include_reflection,
)
from backend.memory.context_builder.text_processing import (
    build_date_formatter,
    build_memory_guidance,
    estimate_tokens,
    is_semantically_covered,
    should_compress,
    tier_ratios,
    to_text,
    truncate_dialog_summary_snippet,
    truncate_retrieved_message_content,
)


logger = logging.getLogger(__name__)


# Re-export all public names so existing imports from runtime_helpers still work
__all__ = [
    "build_date_formatter",
    "build_memory_guidance",
    "build_record_content",
    "build_record_prompt_entry",
    "build_retrieved_message_text",
    "build_semantic_coverage_texts",
    "build_summary_prompt_entry",
    "compact_recent_messages",
    "compute_suppressed_record_ids",
    "estimate_tokens",
    "is_semantically_covered",
    "is_suppressed_record",
    "rank_observations_by_id",
    "rank_reflections_by_id",
    "should_compress",
    "should_include_reflection",
    "tier_ratios",
    "truncate_retrieved_message_content",
]


# ---------------------------------------------------------------------------
# Dialog compression helpers
# ---------------------------------------------------------------------------


def build_dialog_summary_content(
    omitted_messages: list[tuple[str, str, int]],
    records: list[tuple[str, ...]],
    summaries: list[str] | list[tuple[str, ...]],
) -> str:
    """Build a compact system summary for older omitted dialog steps."""
    if records or summaries:
        return (
            "Earlier dialog summary:\n"
            "Older dialog steps were compressed.\n"
            "Use the records and summaries below as the durable record of that "
            "older context."
        )

    lines = [
        "Earlier dialog summary:",
        "Older dialog steps were compressed.",
    ]
    for role, content, _ in omitted_messages[-MAX_DIALOG_SUMMARY_SNIPPETS:]:
        summary_prefix = "User" if role == "user" else "Assistant"
        snippet = truncate_dialog_summary_snippet(content)
        if not snippet:
            continue
        lines.append(f"- {summary_prefix}: {snippet}")
    return "\n".join(lines)


def build_recent_message_costs(
    recent_messages: list[tuple[str, str]],
) -> list[tuple[str, str, int]]:
    """Convert recent messages into text plus estimated token cost."""
    message_costs: list[tuple[str, str, int]] = []
    for role, content in recent_messages:
        text_content = to_text(content)
        message_costs.append((role, text_content, estimate_tokens(text_content)))
    return message_costs


def select_standard_recent_message_entries(
    message_costs: list[tuple[str, str, int]],
    *,
    message_cap: int,
) -> list[ContextEntry]:
    """Return the standard newest-tail message slice within the cap."""
    capped_messages = message_costs
    total_msg_tokens = sum(cost for _, _, cost in capped_messages)
    if total_msg_tokens > message_cap:
        kept: list[tuple[str, str, int]] = []
        running = 0
        for role, content, cost in reversed(message_costs):
            if running + cost > message_cap and kept:
                break
            kept.append((role, content, cost))
            running += cost
        kept.reverse()
        capped_messages = kept
    return [
        ContextEntry(role=role, content=content) for role, content, _ in capped_messages
    ]


def select_tail_entries(
    message_costs: list[tuple[str, str, int]],
    *,
    token_cap: int,
    min_raw_messages_to_keep: int,
) -> list[tuple[str, str, int]]:
    """Keep the newest raw messages within the available token budget."""
    kept_tail: list[tuple[str, str, int]] = []
    running_tail_tokens = 0
    for role, content, cost in reversed(message_costs):
        if (
            running_tail_tokens + cost > token_cap
            and kept_tail
            and len(kept_tail) >= min_raw_messages_to_keep
        ):
            break
        if running_tail_tokens + cost > token_cap and (
            kept_tail or min_raw_messages_to_keep == 0
        ):
            continue
        kept_tail.append((role, content, cost))
        running_tail_tokens += cost
    kept_tail.reverse()
    if not kept_tail and min_raw_messages_to_keep > 0 and message_costs:
        return [message_costs[-1]]
    return kept_tail


def build_compacted_recent_entries(
    *,
    message_costs: list[tuple[str, str, int]],
    kept_tail: list[tuple[str, str, int]],
    records: list[tuple[str, ...]],
    summaries: list[str] | list[tuple[str, ...]],
) -> list[ContextEntry]:
    """Build dialog-summary plus kept tail entries after compression."""
    omitted_count = len(message_costs) - len(kept_tail)
    omitted_messages = message_costs[:omitted_count]
    summary_content = build_dialog_summary_content(
        omitted_messages,
        records,
        summaries,
    )
    summary_entry = ContextEntry(
        role="dialog_summary",
        content=summary_content,
    )
    kept_entries = [
        ContextEntry(role=role, content=content) for role, content, _ in kept_tail
    ]
    return [summary_entry, *kept_entries]


def compact_recent_messages(
    recent_messages: list[tuple[str, str]],
    *,
    token_budget: int,
    message_cap: int,
    records: list[tuple[str, ...]],
    summaries: list[str] | list[tuple[str, ...]],
    memory_query: bool,
) -> list[ContextEntry]:
    """Compress the older dialog tail when raw message history grows too large."""
    message_costs = build_recent_message_costs(recent_messages)
    standard_entries = select_standard_recent_message_entries(
        message_costs,
        message_cap=message_cap,
    )
    total_msg_tokens = sum(cost for _, _, cost in message_costs)
    if token_budget < AUTO_COMPRESSION_MIN_TOKEN_BUDGET:
        return standard_entries

    compression_trigger = int(message_cap * AUTO_COMPRESSION_TRIGGER_RATIO)
    if total_msg_tokens <= compression_trigger:
        return standard_entries

    raw_tail_cap = max(1, int(message_cap * COMPRESSED_MESSAGE_CAP_RATIO))
    min_raw_messages_to_keep = 0 if memory_query else MIN_RAW_MESSAGES_TO_KEEP
    kept_tail = select_tail_entries(
        message_costs,
        token_cap=raw_tail_cap,
        min_raw_messages_to_keep=min_raw_messages_to_keep,
    )

    omitted_count = len(message_costs) - len(kept_tail)
    if omitted_count <= 0:
        return standard_entries

    omitted_messages = message_costs[:omitted_count]
    summary_content = build_dialog_summary_content(
        omitted_messages,
        records,
        summaries,
    )
    summary_cost = estimate_tokens(summary_content)
    tail_budget = 0 if memory_query else max(1, message_cap - summary_cost)
    kept_tail = select_tail_entries(
        message_costs,
        token_cap=tail_budget,
        min_raw_messages_to_keep=min_raw_messages_to_keep,
    )

    omitted_count = len(message_costs) - len(kept_tail)
    if omitted_count <= 0:
        return standard_entries

    omitted_messages = message_costs[:omitted_count]
    return build_compacted_recent_entries(
        message_costs=message_costs,
        kept_tail=kept_tail,
        records=records,
        summaries=summaries,
    )
