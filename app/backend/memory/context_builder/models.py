"""Typed models for assembled memory context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from .constants import (
    CONVERSATION_SUMMARY_ZONE_RATIO,
    DEFAULT_EVICTION_RATIO,
    RECALL_RESULTS_ZONE_RATIO,
    RESPONSE_TOKEN_RESERVE_RATIO,
    WORKING_MEMORY_ZONE_RATIO,
    estimate_tokens,
)


class RetrievedItem(TypedDict, total=False):
    """Structured debug metadata for one retrieved memory entry."""

    summary: str
    date: str
    role: str
    content: str
    conversation_id: str
    main_point: str


@dataclass(frozen=True)
class ContextEntry:
    """A single entry in the assembled LLM context.

    Attributes:
        role: Entry type (summary, record, message).
        content: Text content.
    """

    role: str
    content: str


@dataclass(frozen=True)
class ContextResult:
    """Assembled context with metadata, produced by build_zone_context.

    Attributes:
        entries: Assembled context entries.
        memory_guidance: Optional system guidance for compressed memory.
        retrieved_memories: Summaries of search-matched records.
        retrieved_messages: Debug metadata for FTS-matched messages.
        retrieved_reflections: Debug metadata for retrieved summaries.
    """

    entries: list[ContextEntry]
    retrieved_memories: list[RetrievedItem]
    memory_guidance: str | None = None
    retrieved_messages: list[RetrievedItem] | None = None
    retrieved_reflections: list[RetrievedItem] | None = None
    evicted_messages: list[tuple[str, str]] | None = None
    zone_usage: dict[str, int] | None = None


@dataclass(frozen=True)
class ContextZoneBudget:
    """Token budgets for each context zone.

    Computed from the model's context window size, system prompt,
    and tool definitions. All values are in estimated tokens.

    Attributes:
        working_memory: Max tokens for recent conversation messages.
        recall_results: Max tokens for memory_recall tool output.
        conversation_summary: Max tokens for compressed history.
        response_reserve: Tokens reserved for LLM response.
        total_available: Total tokens available for all zones.
        eviction_ratio: Fraction of W to evict when full (0.0-1.0).
    """

    working_memory: int
    recall_results: int
    conversation_summary: int
    response_reserve: int
    total_available: int
    eviction_ratio: float = DEFAULT_EVICTION_RATIO

    @classmethod
    def from_context_window(
        cls,
        num_ctx: int,
        system_prompt: str = "",
        tool_definitions_text: str = "",
    ) -> ContextZoneBudget:
        """Compute zone budgets from the model context window.

        Args:
            num_ctx: Total model context window in tokens. Must be a strictly
                positive integer; the silent fallback to 4000 has been
                removed and ``None`` or non-positive values are rejected.
            system_prompt: System prompt text for token estimation.
            tool_definitions_text: Serialized tool schemas text.

        Returns:
            Computed zone budgets.

        Raises:
            ValueError: If ``num_ctx`` is not a strictly positive integer.
        """
        if not isinstance(num_ctx, int) or num_ctx <= 0:
            raise ValueError(
                "ContextZoneBudget.from_context_window requires a positive "
                f"integer num_ctx; got {num_ctx!r}",
            )
        fixed_overhead = estimate_tokens(system_prompt) + estimate_tokens(
            tool_definitions_text
        )
        response_reserve = max(
            512,
            int(num_ctx * RESPONSE_TOKEN_RESERVE_RATIO),
        )
        available = max(0, num_ctx - fixed_overhead - response_reserve)
        return cls(
            working_memory=int(available * WORKING_MEMORY_ZONE_RATIO),
            recall_results=int(available * RECALL_RESULTS_ZONE_RATIO),
            conversation_summary=int(
                available * CONVERSATION_SUMMARY_ZONE_RATIO,
            ),
            response_reserve=response_reserve,
            total_available=available,
        )
