"""Four-tier context builder for LLM prompt assembly.

Assembles LLM context from four tiers:
1. Recent messages (raw conversation tail) — mandatory
2. Retrieved messages (FTS-matched from any session) — optional
3. Records (ranked by search relevance or chronological)
4. Summaries (long-term main points)

When search results are provided, records matching
search hits are prioritized over chronological ordering.
Retrieved messages fill the gap between chronological messages
and compressed records, preserving exact details.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Callable
    from zoneinfo import ZoneInfo

    from backend.memory.retrieval import SearchResult

from backend.memory.context_builder.constants import (
    MAX_MEMORY_BUDGET,
    MEMORY_WINDOW_RATIO,
    MIN_MEMORY_BUDGET,
    OPERATIONAL_TOKEN_RESERVE,
)
from backend.memory.context_builder.models import ContextEntry, ContextResult
from backend.memory.context_builder.runtime_helpers import (
    build_date_formatter,
    build_memory_guidance,
    build_record_prompt_entry,
    build_retrieved_message_text,
    build_semantic_coverage_texts,
    build_summary_prompt_entry,
    compact_recent_messages,
    compute_suppressed_record_ids,
    estimate_tokens,
    is_semantically_covered,
    is_suppressed_record,
    rank_observations_by_id,
    rank_reflections_by_id,
    should_compress,
    should_include_reflection,
    tier_ratios,
    truncate_retrieved_message_content,
)
from backend.memory.context_builder.runtime_models import ContextAssemblyState


logger = logging.getLogger(__name__)

# Re-export for backward compatibility via __init__.py
__all__ = [
    "build_context_with_ids",
    "compute_memory_budget",
    "estimate_tokens",
    "should_compress",
]


def compute_memory_budget(llm_context_window: int) -> int:
    """Compute a conservative memory budget from the model context window.

    Args:
        llm_context_window: Strictly positive integer context window from
            resolved capabilities. The silent fallback to 4000 has been
            removed; ``None`` and non-positive values are rejected.

    Raises:
        ValueError: If ``llm_context_window`` is not a positive integer.
    """
    if not isinstance(llm_context_window, int) or llm_context_window <= 0:
        raise ValueError(
            "compute_memory_budget requires a positive integer "
            f"llm_context_window; got {llm_context_window!r}",
        )
    scaled_budget = int(llm_context_window * MEMORY_WINDOW_RATIO)
    reserved_budget = max(1024, llm_context_window - OPERATIONAL_TOKEN_RESERVE)
    return min(
        max(MIN_MEMORY_BUDGET, scaled_budget),
        MAX_MEMORY_BUDGET,
        reserved_budget,
    )


def build_context_with_ids(
    summaries: list[str] | list[tuple[str, ...]],
    records: list[tuple[str, ...]],
    recent_messages: list[tuple[str, str]],
    search_results: list[SearchResult] | None = None,
    token_budget: int | None = None,
    llm_context_window: int | None = None,
    retrieved_messages: list[tuple[str, ...]] | None = None,
    summary_search_results: list[SearchResult] | None = None,
    memory_query: bool = False,
    session_id: str | None = None,
    user_tz: ZoneInfo | None = None,
) -> ContextResult:
    """Build context using record tuples for search ranking.

    Records can be:
    - ``(id, summary)``
    - ``(id, summary, created_at)``
    - ``(id, summary, key_facts, action_items, created_at)``
    Summaries can be plain strings, ``(main_point, created_at)``
    tuples, or enhanced ``(id, main_point, source_record_ids, created_at)``
    tuples.
    Retrieved messages are (id, role, content, conversation_id, created_at)
    tuples from FTS search across all sessions.

    Tier order with cascading budget:
    1. Recent messages (50%)
    2. Retrieved messages (15%) — FTS-matched from any session
    3. Records (25%) — ranked by search relevance
    4. Summaries (10%)

    Unused allocation from each tier cascades to the next.

    Args:
        summaries: List of summary main-point strings or
            (main_point, created_at) tuples.
        records: List of record tuples.
        recent_messages: List of (role, content) tuples.
        search_results: Optional ranked search results.
        token_budget: Maximum tokens for the assembled context.
        llm_context_window: Optional model context window size.
        retrieved_messages: Optional list of (id, role, content,
            conversation_id, created_at) tuples from FTS search.
        summary_search_results: Optional ranked summary search
            results for reordering and labeling long-term summaries.
        memory_query: Whether the user is explicitly asking about
            remembered prior context.
        session_id: Current session ID for labeling retrieved messages.
        user_tz: User's timezone for converting UTC timestamps.
            If None, timestamps are truncated as-is (UTC).

    Returns:
        ContextResult with entries and retrieved memory/message/summary
        debug metadata.
    """
    if token_budget is None:
        if llm_context_window is None:
            raise ValueError(
                "build_context_with_ids requires either an explicit "
                "token_budget or a positive llm_context_window; both were "
                "None. Resolve capabilities via the registry first.",
            )
        token_budget = compute_memory_budget(llm_context_window)

    state = ContextAssemblyState()
    (
        message_ratio,
        retrieved_ratio,
        record_ratio,
        summary_ratio,
    ) = tier_ratios(memory_query)
    message_cap = int(token_budget * message_ratio)
    format_date = build_date_formatter(user_tz)

    ranked, matched_ids = rank_observations_by_id(records, search_results)
    ranked_reflections, matched_summary_ids = rank_reflections_by_id(
        summaries,
        summary_search_results,
        records,
    )
    suppressed_record_ids = compute_suppressed_record_ids(
        records=ranked,
        ranked_reflections=ranked_reflections,
        matched_summary_ids=matched_summary_ids,
        matched_record_ids=matched_ids,
    )

    # 1. Recent messages — capped or summarized to preserve budget for memory tiers.
    _append_recent_message_entries(
        state=state,
        recent_messages=recent_messages,
        token_budget=token_budget,
        message_cap=message_cap,
        records=ranked,
        summaries=ranked_reflections,
        memory_query=memory_query,
    )
    semantic_coverage_texts = build_semantic_coverage_texts(
        records=ranked,
        ranked_reflections=ranked_reflections,
        matched_record_ids=matched_ids,
        matched_summary_ids=matched_summary_ids,
        suppressed_record_ids=suppressed_record_ids,
        memory_query=memory_query,
    )

    # Cascading budget: unused message allocation flows to retrieved messages
    msg_unused = max(0, message_cap - state.used_tokens)
    retrieved_cap = int(token_budget * retrieved_ratio) + msg_unused

    # 2. Retrieved messages — FTS-matched from any session
    _append_retrieved_message_entries(
        state=state,
        retrieved_messages=retrieved_messages,
        retrieved_cap=retrieved_cap,
        memory_query=memory_query,
        semantic_coverage_texts=semantic_coverage_texts,
        session_id=session_id,
        format_date=format_date,
    )

    # Cascading: unused retrieved allocation flows to records
    retrieved_actual = sum(
        estimate_tokens(entry.content)
        for entry in state.entries
        if entry.role == "retrieved_message"
    )
    retrieved_unused = max(0, retrieved_cap - retrieved_actual)
    obs_cap = int(token_budget * record_ratio) + retrieved_unused
    obs_unused = _append_record_entries(
        state=state,
        ranked_records=ranked,
        suppressed_record_ids=suppressed_record_ids,
        matched_record_ids=matched_ids,
        obs_cap=obs_cap,
        token_budget=token_budget,
        format_date=format_date,
    )
    summary_cap = int(token_budget * summary_ratio) + obs_unused
    _append_summary_entries(
        state=state,
        ranked_reflections=ranked_reflections,
        matched_summary_ids=matched_summary_ids,
        summary_cap=summary_cap,
        token_budget=token_budget,
        memory_query=memory_query,
        format_date=format_date,
    )

    return _build_context_result(
        state=state,
        ranked_reflections=ranked_reflections,
    )


def _build_context_result(
    *,
    state: ContextAssemblyState,
    ranked_reflections: list[str] | list[tuple[str, ...]],
) -> ContextResult:
    """Build the final context-builder result from rolling state."""
    return ContextResult(
        entries=state.entries,
        retrieved_memories=state.retrieved_memories,
        memory_guidance=build_memory_guidance(
            entries=state.entries,
            summaries=ranked_reflections,
        ),
        retrieved_messages=state.retrieved_msg_debug or None,
        retrieved_reflections=state.retrieved_summary_debug or None,
    )


def _append_record_entries(
    state: ContextAssemblyState,
    *,
    ranked_records: list[tuple[str, ...]],
    suppressed_record_ids: set[str],
    matched_record_ids: set[str],
    obs_cap: int,
    token_budget: int,
    format_date: Callable[[str], str],
) -> int:
    """Append the record tier and return unused record budget."""
    obs_used = 0
    for record in ranked_records:
        if is_suppressed_record(record, suppressed_record_ids):
            continue
        prompt_entry = build_record_prompt_entry(
            record=record,
            matched_record_ids=matched_record_ids,
            format_date=format_date,
        )
        if (
            obs_used + prompt_entry.cost > obs_cap
            or state.used_tokens + prompt_entry.cost > token_budget
        ):
            break
        state.entries.append(ContextEntry(role="record", content=prompt_entry.content))
        state.used_tokens += prompt_entry.cost
        obs_used += prompt_entry.cost
        if prompt_entry.is_matched:
            state.retrieved_memories.append(
                {"summary": prompt_entry.text, "date": prompt_entry.date_str}
            )
    return max(0, obs_cap - obs_used)


def _append_summary_entries(
    state: ContextAssemblyState,
    *,
    ranked_reflections: list[str] | list[tuple[str, ...]],
    matched_summary_ids: set[str],
    summary_cap: int,
    token_budget: int,
    memory_query: bool,
    format_date: Callable[[str], str],
) -> None:
    """Append the summary tier to the rolling context state."""
    summary_used = 0
    for summary in ranked_reflections:
        if not should_include_reflection(summary, memory_query):
            continue
        prompt_entry = build_summary_prompt_entry(
            summary=summary,
            matched_summary_ids=matched_summary_ids,
            format_date=format_date,
        )
        if prompt_entry.cost + summary_used > summary_cap and summary_used > 0:
            break
        if state.used_tokens + prompt_entry.cost > token_budget:
            break
        state.entries.append(ContextEntry(role="summary", content=prompt_entry.content))
        state.used_tokens += prompt_entry.cost
        summary_used += prompt_entry.cost
        if prompt_entry.is_matched:
            state.retrieved_summary_debug.append(
                {"main_point": prompt_entry.text, "date": prompt_entry.date_str}
            )


def _append_recent_message_entries(
    state: ContextAssemblyState,
    recent_messages: list[tuple[str, str]],
    *,
    token_budget: int,
    message_cap: int,
    records: list[tuple[str, ...]],
    summaries: list[str] | list[tuple[str, ...]],
    memory_query: bool,
) -> None:
    """Append the compacted recent-message tier to the rolling state."""
    message_entries = compact_recent_messages(
        recent_messages,
        token_budget=token_budget,
        message_cap=message_cap,
        records=records,
        summaries=summaries,
        memory_query=memory_query,
    )
    for entry in message_entries:
        state.entries.append(entry)
        state.used_tokens += estimate_tokens(entry.content)


def _append_retrieved_message_entries(
    state: ContextAssemblyState,
    *,
    retrieved_messages: list[tuple[str, ...]] | None,
    retrieved_cap: int,
    memory_query: bool,
    semantic_coverage_texts: list[str],
    session_id: str | None,
    format_date: Callable[[str], str],
) -> None:
    """Append the retrieved-message tier to the rolling context state."""
    if not retrieved_messages:
        return

    retrieved_used = 0
    for retrieved_message in retrieved_messages:
        message_role = retrieved_message[1]
        message_content = truncate_retrieved_message_content(retrieved_message[2])
        if memory_query and is_semantically_covered(
            message_content,
            semantic_coverage_texts,
        ):
            continue

        conversation_identifier = (
            retrieved_message[3] if len(retrieved_message) > 3 else ""
        )
        message_date = (
            format_date(retrieved_message[4]) if len(retrieved_message) > 4 else ""
        )
        text = build_retrieved_message_text(
            message_content=message_content,
            conversation_identifier=conversation_identifier,
            session_id=session_id,
            message_date=message_date,
        )
        cost = estimate_tokens(text)
        if retrieved_used + cost > retrieved_cap and retrieved_used > 0:
            break

        state.entries.append(ContextEntry(role="retrieved_message", content=text))
        state.used_tokens += cost
        retrieved_used += cost
        state.retrieved_msg_debug.append(
            {
                "role": message_role,
                "content": message_content[:100],
                "conversation_id": conversation_identifier,
                "date": message_date,
            }
        )
