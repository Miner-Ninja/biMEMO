"""Memory context loading delegate for the gateway orchestrator.

Loads and routes memory context for conversation turns, including
raw context fetching, normalization into the prepared context shape,
memory question routing with fallback classification, and diagnostic
persistence. Works with both sync and async context callables.
"""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING

from backend.agent.agent_loop_models import (
    MemoryRouteDecision,
    PreparedContext,
)
from backend.memory.classification.fallback_classifier import (
    classify_memory_question_with_llm_call,
)
from backend.memory.classification.fallback_policy import (
    semantic_fallback_reason,
)
from backend.memory.classification.models import ClassificationConfidence
from backend.memory.classification.question_router import (
    MemoryQuestionRoute,
    route_memory_question,
)
from backend.memory.classification.trace import (
    MemoryQuestionRouteTrace,
)
from backend.memory.context_builder import ContextResult
from backend.processes.orchestrator.client import DBProxyError


if TYPE_CHECKING:
    from collections.abc import Callable


logger = logging.getLogger(__name__)

# --- Recoverable errors for memory callbacks ---
RECOVERABLE_MEMORY_CALLBACK_ERRORS = (
    DBProxyError,
    RuntimeError,
    TypeError,
    ValueError,
)


class MemoryContextLoader:
    """Load and route memory context for conversation turns.

    Attributes:
        context_fn: Callable returning context entries.
        evidence_bundle_fn: Optional callable returning evidence bundles.
        write_diagnostic_fn: Optional callable for memory route diagnostics.
    """

    def __init__(
        self,
        context_fn: Callable,
        evidence_bundle_fn: Callable | None,
        write_diagnostic_fn: Callable | None,
    ) -> None:
        """Initialize memory context loader.

        Args:
            context_fn: Callable returning context entries.
            evidence_bundle_fn: Optional callable returning evidence bundles.
            write_diagnostic_fn: Optional callable for diagnostics.
        """
        self.context_fn = context_fn
        self.evidence_bundle_fn = evidence_bundle_fn
        self.write_diagnostic_fn = write_diagnostic_fn

    async def load_prepared_context(
        self,
        *,
        session_id: str,
        filtered_message: str,
        conversation_id: str,
    ) -> PreparedContext:
        """Load memory context and normalize it into one shape."""
        context_raw = await self.load_raw_context(
            session_id=session_id,
            filtered_message=filtered_message,
            conversation_id=conversation_id,
        )
        prepared_context = self.normalize_prepared_context(context_raw)
        self.log_prepared_context(context_raw, prepared_context)
        return prepared_context

    async def load_raw_context(
        self,
        *,
        session_id: str,
        filtered_message: str,
        conversation_id: str,
    ) -> object:
        """Load one raw context payload from the configured context source."""
        if inspect.iscoroutinefunction(self.context_fn):
            return await self.context_fn(
                session_id,
                query=filtered_message,
                conversation_id=conversation_id,
            )
        return self.context_fn(
            session_id,
            query=filtered_message,
            conversation_id=conversation_id,
        )

    def normalize_prepared_context(
        self,
        context_raw: object,
    ) -> PreparedContext:
        """Normalize one raw context payload into the prepared context shape."""
        if isinstance(context_raw, ContextResult):
            return PreparedContext(
                entries=context_raw.entries,
                retrieved_memories=context_raw.retrieved_memories,
                retrieved_messages=context_raw.retrieved_messages,
                memory_guidance=context_raw.memory_guidance,
                zone_usage=context_raw.zone_usage,
                zone_budget=context_raw.zone_budget,
            )
        return PreparedContext(
            entries=context_raw,
            retrieved_memories=[],
            retrieved_messages=None,
            memory_guidance=None,
        )

    def log_prepared_context(
        self,
        context_raw: object,
        prepared_context: PreparedContext,
    ) -> None:
        """Write one debug log entry about the loaded prompt context."""
        logger.debug(
            "Context: type=%s, entries=%d, retrieved_memories=%d",
            type(context_raw).__name__,
            len(prepared_context.entries),
            len(prepared_context.retrieved_memories),
        )

    async def prepare_turn_memory_context(
        self,
        *,
        llm_call: object,
        session_id: str,
        conversation_id: str,
        filtered_message: str,
        user_message_id: str | None,
        channel: str,
        messages: list[dict[str, object]],
        prepared_context: PreparedContext,
        append_memory_context_fn: Callable,
    ) -> MemoryQuestionRoute:
        """Resolve memory routing, persist diagnostics, and extend prompt messages."""
        route_decision = await self.resolve_memory_route(
            llm_call=llm_call,
            filtered_message=filtered_message,
        )
        question_route = route_decision.route
        await self.persist_memory_route_diagnostic(
            session_id=session_id,
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            channel=channel,
            route_trace=route_decision.trace,
            used_long_term_mode=question_route.long_term_memory_question,
        )
        await append_memory_context_fn(
            messages=messages,
            filtered_message=filtered_message,
            prepared_context=prepared_context,
            route=question_route,
        )
        return question_route

    async def resolve_memory_route(
        self,
        *,
        llm_call: object,
        filtered_message: str,
    ) -> MemoryRouteDecision:
        """Choose the final memory route for one user message."""
        question_route = route_memory_question(filtered_message)
        initial_question_route = question_route
        fallback_route: MemoryQuestionRoute | None = None
        fallback_reason = semantic_fallback_reason(
            question_route,
            filtered_message,
        )
        if fallback_reason is not None:
            fallback_route = await self.resolve_fallback_memory_route(
                llm_call=llm_call,
                filtered_message=filtered_message,
                fallback_reason=fallback_reason,
                initial_question_route=initial_question_route,
            )
            if fallback_route.confidence in {
                ClassificationConfidence.HIGH,
                ClassificationConfidence.MEDIUM,
            }:
                question_route = fallback_route

        route_trace = MemoryQuestionRouteTrace(
            initial_route=initial_question_route,
            final_route=question_route,
            fallback_attempted=fallback_reason is not None,
            fallback_reason=fallback_reason,
            fallback_route=fallback_route,
        )
        logger.info(
            (
                "Memory routing decision: source=%s intent=%s confidence=%s "
                "role_filter=%s fallback_recommended=%s fallback_reason=%s "
                "route_mismatch=%s mismatch_label=%s signal_groups=%s"
            ),
            question_route.source,
            question_route.intent.value,
            question_route.confidence.value,
            question_route.role_filter or "none",
            initial_question_route.fallback_recommended,
            fallback_reason or "none",
            route_trace.route_mismatch,
            route_trace.mismatch_label,
            ",".join(question_route.matched_signal_groups) or "none",
        )
        logger.info("Memory routing shadow summary: %s", route_trace.to_dict())
        return MemoryRouteDecision(
            route=question_route,
            trace=route_trace,
        )

    async def resolve_fallback_memory_route(
        self,
        *,
        llm_call: object,
        filtered_message: str,
        fallback_reason: str,
        initial_question_route: MemoryQuestionRoute,
    ) -> MemoryQuestionRoute:
        """Ask the fallback classifier for a second memory-routing opinion."""
        fallback_result = await classify_memory_question_with_llm_call(
            llm_call,
            filtered_message,
        )
        fallback_route = self.build_fallback_memory_route(
            fallback_result=fallback_result,
        )
        override_applied = fallback_route.confidence in {
            ClassificationConfidence.HIGH,
            ClassificationConfidence.MEDIUM,
        }
        self.log_memory_route_fallback_decision(
            fallback_reason=fallback_reason,
            initial_question_route=initial_question_route,
            fallback_route=fallback_route,
            override_applied=override_applied,
        )
        return fallback_route

    def build_fallback_memory_route(
        self,
        *,
        fallback_result: object,
    ) -> MemoryQuestionRoute:
        """Convert the fallback classifier result into a memory route."""
        fallback_classification = fallback_result.classification
        return MemoryQuestionRoute(
            role_filter=fallback_classification.role_filter,
            memory_query=fallback_classification.memory_query,
            long_term_memory_question=(
                fallback_classification.long_term_memory_question
            ),
            intent=fallback_classification.intent,
            confidence=fallback_classification.confidence,
            fallback_recommended=False,
            source="semantic_fallback",
            matched_signal_groups=(),
            fallback_rationale=fallback_result.rationale or None,
        )

    def log_memory_route_fallback_decision(
        self,
        *,
        fallback_reason: str,
        initial_question_route: MemoryQuestionRoute,
        fallback_route: MemoryQuestionRoute,
        override_applied: bool,
    ) -> None:
        """Log whether fallback routing replaced the original memory route."""
        if override_applied:
            logger.info(
                (
                    "Memory routing fallback override: "
                    "reason=%s "
                    "signal_intent=%s signal_confidence=%s "
                    "fallback_intent=%s fallback_confidence=%s"
                ),
                fallback_reason,
                initial_question_route.intent.value,
                initial_question_route.confidence.value,
                fallback_route.intent.value,
                fallback_route.confidence.value,
            )
            return
        logger.info(
            (
                "Memory routing fallback kept signal route: "
                "reason=%s signal_intent=%s signal_confidence=%s "
                "fallback_intent=%s fallback_confidence=%s"
            ),
            fallback_reason,
            initial_question_route.intent.value,
            initial_question_route.confidence.value,
            fallback_route.intent.value,
            fallback_route.confidence.value,
        )

    async def persist_memory_route_diagnostic(
        self,
        *,
        session_id: str,
        conversation_id: str,
        user_message_id: str | None,
        channel: str,
        route_trace: MemoryQuestionRouteTrace,
        used_long_term_mode: bool,
    ) -> None:
        """Persist the memory routing trace when diagnostics are enabled."""
        if self.write_diagnostic_fn is None:
            return
        diagnostic_payload = route_trace.to_dict()
        try:
            if inspect.iscoroutinefunction(self.write_diagnostic_fn):
                await self.write_diagnostic_fn(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    message_id=user_message_id or "",
                    channel=channel,
                    route_trace=diagnostic_payload,
                    used_long_term_mode=used_long_term_mode,
                )
            else:
                self.write_diagnostic_fn(
                    session_id=session_id,
                    conversation_id=conversation_id,
                    message_id=user_message_id or "",
                    channel=channel,
                    route_trace=diagnostic_payload,
                    used_long_term_mode=used_long_term_mode,
                )
        except RECOVERABLE_MEMORY_CALLBACK_ERRORS:
            logger.warning(
                "Failed to persist memory-route diagnostic",
                exc_info=True,
            )
