"""Diagnostic DB Proxy operations for memory-route, record, and summary processing.

Provides module-level async functions that persist structured diagnostic
records through DBProxyClient. Each function receives a DBProxyClient
instance as its first parameter. Pure parameter-building helpers are also
included here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from backend.processes.orchestrator.client import DBProxyClient


async def write_memory_route_diagnostic(
    client: DBProxyClient,
    *,
    session_id: str,
    conversation_id: str,
    message_id: str,
    channel: str,
    initial_intent: str,
    initial_confidence: str,
    initial_role_filter: str | None,
    initial_source: str,
    initial_signal_groups: list[str],
    fallback_attempted: bool,
    fallback_reason: str | None,
    fallback_intent: str | None,
    fallback_confidence: str | None,
    fallback_role_filter: str | None,
    fallback_source: str | None,
    final_intent: str,
    final_confidence: str,
    final_role_filter: str | None,
    final_source: str,
    final_signal_groups: list[str],
    route_mismatch: bool,
    mismatch_label: str,
    used_long_term_mode: bool,
) -> int:
    """Persist one structured memory-route diagnostic record.

    Args:
        client: DB Proxy client instance.
        session_id: Session identifier.
        conversation_id: Conversation identifier.
        message_id: Message identifier.
        channel: Channel name.
        initial_intent: Initial routing intent.
        initial_confidence: Initial confidence level.
        initial_role_filter: Initial role filter or None.
        initial_source: Initial routing source.
        initial_signal_groups: Initial signal group list.
        fallback_attempted: Whether fallback was attempted.
        fallback_reason: Reason for fallback or None.
        fallback_intent: Fallback intent or None.
        fallback_confidence: Fallback confidence or None.
        fallback_role_filter: Fallback role filter or None.
        fallback_source: Fallback source or None.
        final_intent: Final resolved intent.
        final_confidence: Final confidence level.
        final_role_filter: Final role filter or None.
        final_source: Final routing source.
        final_signal_groups: Final signal group list.
        route_mismatch: Whether a route mismatch was detected.
        mismatch_label: Mismatch description label.
        used_long_term_mode: Whether long-term mode was used.

    Returns:
        Diagnostic record ID.
    """
    return await client._send_int_operation(
        "write_memory_route_diagnostic",
        result_key="diagnostic_id",
        parameters=build_memory_route_diagnostic_params(
            session_id=session_id,
            conversation_id=conversation_id,
            message_id=message_id,
            channel=channel,
            initial_intent=initial_intent,
            initial_confidence=initial_confidence,
            initial_role_filter=initial_role_filter,
            initial_source=initial_source,
            initial_signal_groups=initial_signal_groups,
            fallback_attempted=fallback_attempted,
            fallback_reason=fallback_reason,
            fallback_intent=fallback_intent,
            fallback_confidence=fallback_confidence,
            fallback_role_filter=fallback_role_filter,
            fallback_source=fallback_source,
            final_intent=final_intent,
            final_confidence=final_confidence,
            final_role_filter=final_role_filter,
            final_source=final_source,
            final_signal_groups=final_signal_groups,
            route_mismatch=route_mismatch,
            mismatch_label=mismatch_label,
            used_long_term_mode=used_long_term_mode,
        ),
    )


async def write_record_processing_diagnostic(
    client: DBProxyClient,
    *,
    decision: str,
    conversation_id: str,
    session_id: str,
    record_id: str = "",
    model: str = "",
    message_count: int = 0,
    source_message_ids: list[str] | None = None,
    messages: list[str] | None = None,
    injection_flagged: bool = False,
    summary: str = "",
    key_facts: list[str] | None = None,
    action_items: list[str] | None = None,
    memory_kinds: list[str] | None = None,
    prompt: list[dict[str, Any]] | None = None,
    raw_output: str = "",
    error: str = "",
    detail: str = "",
) -> int:
    """Persist one structured record-processing diagnostic record.

    Args:
        client: DB Proxy client instance.
        decision: Processing decision label.
        conversation_id: Conversation identifier.
        session_id: Session identifier.
        record_id: Record identifier.
        model: Model name used.
        message_count: Number of messages processed.
        source_message_ids: Source message identifiers.
        messages: Message texts.
        injection_flagged: Whether injection was flagged.
        summary: Summary text.
        key_facts: Extracted key facts.
        action_items: Extracted action items.
        memory_kinds: Memory category labels.
        prompt: Prompt messages sent to LLM.
        raw_output: Raw LLM output.
        error: Error message if any.
        detail: Additional detail text.

    Returns:
        Diagnostic record ID.
    """
    return await client._send_int_operation(
        "write_record_processing_diagnostic",
        result_key="diagnostic_id",
        parameters=build_record_processing_diagnostic_params(
            decision=decision,
            conversation_id=conversation_id,
            session_id=session_id,
            record_id=record_id,
            model=model,
            message_count=message_count,
            source_message_ids=source_message_ids,
            messages=messages,
            injection_flagged=injection_flagged,
            summary=summary,
            key_facts=key_facts,
            action_items=action_items,
            memory_kinds=memory_kinds,
            prompt=prompt,
            raw_output=raw_output,
            error=error,
            detail=detail,
        ),
    )


async def write_summary_processing_diagnostic(
    client: DBProxyClient,
    *,
    decision: str,
    summary_id: str = "",
    model: str = "",
    record_count: int = 0,
    source_record_ids: list[str] | None = None,
    records: list[dict[str, Any]] | None = None,
    prompt: list[dict[str, Any]] | None = None,
    raw_output: str = "",
    main_point: str = "",
    based_on: list[str] | None = None,
    trust_level: str = "",
    scope: str = "",
    record_relations: list[str] | None = None,
    original_scope: str = "",
    scope_adjustment_reason: str = "",
    error: str = "",
    detail: str = "",
) -> int:
    """Persist one structured summary-processing diagnostic record.

    Args:
        client: DB Proxy client instance.
        decision: Processing decision label.
        summary_id: Summary identifier.
        model: Model name used.
        record_count: Number of records processed.
        source_record_ids: Source record identifiers.
        records: Record data dicts.
        prompt: Prompt messages sent to LLM.
        raw_output: Raw LLM output.
        main_point: Summary main point.
        based_on: Supporting reasons.
        trust_level: Trust level label.
        scope: Summary scope.
        record_relations: Typed relations to records.
        original_scope: Original scope before adjustment.
        scope_adjustment_reason: Reason for scope change.
        error: Error message if any.
        detail: Additional detail text.

    Returns:
        Diagnostic record ID.
    """
    return await client._send_int_operation(
        "write_summary_processing_diagnostic",
        result_key="diagnostic_id",
        parameters=build_summary_processing_diagnostic_params(
            decision=decision,
            summary_id=summary_id,
            model=model,
            record_count=record_count,
            source_record_ids=source_record_ids,
            records=records,
            prompt=prompt,
            raw_output=raw_output,
            main_point=main_point,
            based_on=based_on,
            trust_level=trust_level,
            scope=scope,
            record_relations=record_relations,
            original_scope=original_scope,
            scope_adjustment_reason=scope_adjustment_reason,
            error=error,
            detail=detail,
        ),
    )


def build_memory_route_diagnostic_params(
    *,
    session_id: str,
    conversation_id: str,
    message_id: str,
    channel: str,
    initial_intent: str,
    initial_confidence: str,
    initial_role_filter: str | None,
    initial_source: str,
    initial_signal_groups: list[str],
    fallback_attempted: bool,
    fallback_reason: str | None,
    fallback_intent: str | None,
    fallback_confidence: str | None,
    fallback_role_filter: str | None,
    fallback_source: str | None,
    final_intent: str,
    final_confidence: str,
    final_role_filter: str | None,
    final_source: str,
    final_signal_groups: list[str],
    route_mismatch: bool,
    mismatch_label: str,
    used_long_term_mode: bool,
) -> dict[str, Any]:
    """Build the parameter payload for one memory-route diagnostic write."""
    return {
        "session_id": session_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "channel": channel,
        "initial_intent": initial_intent,
        "initial_confidence": initial_confidence,
        "initial_role_filter": initial_role_filter,
        "initial_source": initial_source,
        "initial_signal_groups": initial_signal_groups,
        "fallback_attempted": fallback_attempted,
        "fallback_reason": fallback_reason,
        "fallback_intent": fallback_intent,
        "fallback_confidence": fallback_confidence,
        "fallback_role_filter": fallback_role_filter,
        "fallback_source": fallback_source,
        "final_intent": final_intent,
        "final_confidence": final_confidence,
        "final_role_filter": final_role_filter,
        "final_source": final_source,
        "final_signal_groups": final_signal_groups,
        "route_mismatch": route_mismatch,
        "mismatch_label": mismatch_label,
        "used_long_term_mode": used_long_term_mode,
    }


def build_record_processing_diagnostic_params(
    *,
    decision: str,
    conversation_id: str,
    session_id: str,
    record_id: str,
    model: str,
    message_count: int,
    source_message_ids: list[str] | None,
    messages: list[str] | None,
    injection_flagged: bool,
    summary: str,
    key_facts: list[str] | None,
    action_items: list[str] | None,
    memory_kinds: list[str] | None,
    prompt: list[dict[str, Any]] | None,
    raw_output: str,
    error: str,
    detail: str,
) -> dict[str, Any]:
    """Build the parameter payload for one record-processing diagnostic write."""
    return {
        "decision": decision,
        "conversation_id": conversation_id,
        "session_id": session_id,
        "record_id": record_id,
        "model": model,
        "message_count": message_count,
        "source_message_ids": source_message_ids or [],
        "messages": messages or [],
        "injection_flagged": injection_flagged,
        "summary": summary,
        "key_facts": key_facts or [],
        "action_items": action_items or [],
        "memory_kinds": memory_kinds or [],
        "prompt": prompt or [],
        "raw_output": raw_output,
        "error": error,
        "detail": detail,
    }


def build_summary_processing_diagnostic_params(
    *,
    decision: str,
    summary_id: str,
    model: str,
    record_count: int,
    source_record_ids: list[str] | None,
    records: list[dict[str, Any]] | None,
    prompt: list[dict[str, Any]] | None,
    raw_output: str,
    main_point: str,
    based_on: list[str] | None,
    trust_level: str,
    scope: str,
    record_relations: list[str] | None,
    original_scope: str,
    scope_adjustment_reason: str,
    error: str,
    detail: str,
) -> dict[str, Any]:
    """Build the parameter payload for one summary-processing diagnostic write."""
    return {
        "decision": decision,
        "summary_id": summary_id,
        "model": model,
        "record_count": record_count,
        "source_record_ids": source_record_ids or [],
        "records": records or [],
        "prompt": prompt or [],
        "raw_output": raw_output,
        "main_point": main_point,
        "based_on": based_on or [],
        "trust_level": trust_level,
        "scope": scope,
        "record_relations": record_relations or [],
        "original_scope": original_scope,
        "scope_adjustment_reason": scope_adjustment_reason,
        "error": error,
        "detail": detail,
    }
