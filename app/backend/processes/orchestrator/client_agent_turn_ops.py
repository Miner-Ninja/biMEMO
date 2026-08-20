"""Core-authority agent turn DB Proxy operations.

Provides module-level async functions that wrap DBProxyClient.send_operation
for the structured turn machinery: authoritative turn lifecycle, tool
invocation claims and results, confirmations, clarifications, pending
inputs, turn events, and projection bookkeeping. Each function receives a
DBProxyClient instance as its first parameter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from backend.processes.orchestrator.client import DBProxyClient


async def create_agent_turn(
    client: DBProxyClient,
    *,
    conversation_id: str,
    session_id: str,
    channel: str,
    turn_id: str | None = None,
    finalization_id: str | None = None,
) -> dict[str, Any]:
    """Create or replay one architecture-pinned authoritative turn.

    Args:
        client: DB Proxy client instance.
        conversation_id: Conversation the turn belongs to.
        session_id: Session the turn belongs to.
        channel: Channel the turn runs on.
        turn_id: Stable turn ID (required for replay).
        finalization_id: Finalization identity (replay marker).

    Returns:
        Turn creation or replay result.
    """
    return await client.send_operation(
        "create_agent_turn",
        {
            "turn_id": turn_id,
            "finalization_id": finalization_id,
            "conversation_id": conversation_id,
            "session_id": session_id,
            "channel": channel,
        },
    )


async def get_agent_turn(
    client: DBProxyClient,
    turn_id: str,
) -> dict[str, Any] | None:
    """Read canonical core turn state and its complete ledger.

    Args:
        client: DB Proxy client instance.
        turn_id: Turn to read.

    Returns:
        Turn state dict, or None if the turn does not exist.
    """
    return await client._send_optional_dict_operation(
        "get_agent_turn",
        result_key="turn",
        parameters={"turn_id": turn_id},
    )


async def upsert_agent_turn_obligations(
    client: DBProxyClient,
    turn_id: str,
    obligations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Idempotently persist mandatory evidence records.

    Args:
        client: DB Proxy client instance.
        turn_id: Turn the obligations belong to.
        obligations: Obligation records to upsert.

    Returns:
        Upsert result.
    """
    return await client.send_operation(
        "upsert_agent_turn_obligations",
        {"turn_id": turn_id, "obligations": obligations},
    )


async def upsert_agent_tool_batch(
    client: DBProxyClient,
    *,
    turn_id: str,
    batch_id: str,
    assistant_message: dict[str, Any],
    transcript_snapshot: list[dict[str, Any]],
    invocations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Persist one complete native tool-call batch.

    Args:
        client: DB Proxy client instance.
        turn_id: Turn that produced the batch.
        batch_id: Stable batch identifier.
        assistant_message: Assistant message that requested the calls.
        transcript_snapshot: Transcript up to and including the batch.
        invocations: Tool invocation records of the batch.

    Returns:
        Batch upsert result.
    """
    return await client.send_operation(
        "upsert_agent_tool_batch",
        {
            "turn_id": turn_id,
            "batch_id": batch_id,
            "assistant_message": assistant_message,
            "transcript_snapshot": transcript_snapshot,
            "invocations": invocations,
        },
    )


async def claim_agent_invocation(
    client: DBProxyClient,
    *,
    turn_id: str,
    invocation_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Commit the durable execution claim before broker dispatch.

    Args:
        client: DB Proxy client instance.
        turn_id: Turn containing the invocation.
        invocation_id: Stable invocation identifier.
        idempotency_key: Idempotency key for the claim.

    Returns:
        Claim result with claim generation.
    """
    parameters: dict[str, Any] = {
        "turn_id": turn_id,
        "invocation_id": invocation_id,
        "idempotency_key": idempotency_key,
    }
    return await client.send_operation("claim_agent_invocation", parameters)


async def complete_agent_invocation(
    client: DBProxyClient,
    *,
    turn_id: str,
    invocation_id: str,
    claim_generation: int,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Record a generation-fenced result and compatible success binds.

    Args:
        client: DB Proxy client instance.
        turn_id: Turn containing the invocation.
        invocation_id: Stable invocation identifier.
        claim_generation: Claim generation being fenced.
        status: Final status (success/error).
        result: Success result payload, if any.
        error: Error text, if any.

    Returns:
        Completion result.
    """
    return await client.send_operation(
        "complete_agent_invocation",
        {
            "turn_id": turn_id,
            "invocation_id": invocation_id,
            "claim_generation": claim_generation,
            "status": status,
            "result": result,
            "error": error,
        },
    )


async def decide_agent_confirmation(
    client: DBProxyClient,
    confirmation_id: str,
    decision: str,
) -> dict[str, Any]:
    """CAS a canonical confirmation decision in core authority.

    Args:
        client: DB Proxy client instance.
        confirmation_id: Confirmation to decide.
        decision: Decision value (approve/deny).

    Returns:
        Decision result.
    """
    return await client.send_operation(
        "decide_agent_confirmation",
        {"confirmation_id": confirmation_id, "decision": decision},
    )


async def get_agent_confirmation(
    client: DBProxyClient,
    confirmation_id: str,
) -> dict[str, Any] | None:
    """Read one canonical confirmation without mutating it.

    Args:
        client: DB Proxy client instance.
        confirmation_id: Confirmation to read.

    Returns:
        Confirmation dict, or None if it does not exist.
    """
    return await client._send_optional_dict_operation(
        "get_agent_confirmation",
        result_key="confirmation",
        parameters={"confirmation_id": confirmation_id},
    )


async def list_pending_agent_confirmations(
    client: DBProxyClient,
    conversation_id: str,
) -> list[dict[str, Any]]:
    """Return pending canonical confirmations for one conversation.

    Args:
        client: DB Proxy client instance.
        conversation_id: Conversation to query.

    Returns:
        Pending confirmation dicts.
    """
    result = await client.send_operation(
        "list_pending_agent_confirmations",
        {"conversation_id": conversation_id},
    )
    return list(result.get("confirmations", []))


async def append_agent_turn_event(
    client: DBProxyClient,
    *,
    turn_id: str,
    event_type: str,
    phase: str = "",
    attempt: int = 0,
    payload: dict[str, Any] | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    """Append an authoritative transition/provider-attempt outbox event.

    Args:
        client: DB Proxy client instance.
        turn_id: Turn the event belongs to.
        event_type: Stable event type name.
        phase: Turn phase marker.
        attempt: Provider attempt number.
        payload: Event payload.
        event_id: Stable event ID (idempotent append).

    Returns:
        Appended event record.
    """
    return await client.send_operation(
        "append_agent_turn_event",
        {
            "turn_id": turn_id,
            "event_id": event_id,
            "event_type": event_type,
            "phase": phase,
            "attempt": attempt,
            "payload": payload or {},
        },
    )


async def cancel_agent_turn(
    client: DBProxyClient,
    turn_id: str,
    *,
    reason: str = "cancelled",
    timeout: bool = False,  # noqa: ASYNC109 — boolean "is this a timeout" flag, not a duration
    failure: bool = False,
    task_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve cancellation against the canonical run-state CAS domain.

    Args:
        client: DB Proxy client instance.
        turn_id: Turn to cancel.
        reason: Cancellation reason.
        timeout: True when the cancellation is a timeout.
        failure: True when the cancellation is a provider failure.
        task_result: Final task result, if any.

    Returns:
        Cancellation result.
    """
    return await client.send_operation(
        "cancel_agent_turn",
        {
            "turn_id": turn_id,
            "reason": reason,
            "timeout": timeout,
            "failure": failure,
            "task_result": task_result,
        },
    )


async def commit_agent_turn_finalization(
    client: DBProxyClient,
    *,
    turn_id: str,
    finalization_id: str,
    outcome_kind: str,
    conversation_id: str,
    session_id: str,
    assistant_content: str,
    assistant_meta: dict[str, Any] | None,
    dialog_content: str,
    source_message_ids: list[str],
    outcome_payload: dict[str, Any],
    persist_chat: bool = True,
    task_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically commit interactive FINAL or FAILED persistence.

    Args:
        client: DB Proxy client instance.
        turn_id: Turn being finalized.
        finalization_id: Finalization identity (idempotency).
        outcome_kind: Outcome kind (FINAL/FAILED).
        conversation_id: Conversation being finalized.
        session_id: Session being finalized.
        assistant_content: Final assistant content.
        assistant_meta: Final assistant metadata.
        dialog_content: Final dialog content.
        source_message_ids: Message IDs merged into the final state.
        outcome_payload: Structured outcome payload.
        persist_chat: Whether to persist the chat messages.
        task_result: Final task result, if any.

    Returns:
        Commit result.
    """
    return await client.send_operation(
        "commit_agent_turn_finalization",
        {
            "turn_id": turn_id,
            "finalization_id": finalization_id,
            "outcome_kind": outcome_kind,
            "conversation_id": conversation_id,
            "session_id": session_id,
            "assistant_content": assistant_content,
            "assistant_meta": assistant_meta,
            "dialog_content": dialog_content,
            "source_message_ids": source_message_ids,
            "outcome_payload": outcome_payload,
            "persist_chat": persist_chat,
            "task_result": task_result,
        },
    )


async def list_unprojected_agent_turn_events(
    client: DBProxyClient,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Read stable events awaiting optional runtime projection.

    Args:
        client: DB Proxy client instance.
        limit: Maximum events returned.

    Returns:
        Unprojected event rows.
    """
    return await client._send_list_operation(
        "list_unprojected_agent_turn_events",
        result_key="events",
        parameters={"limit": limit},
    )


async def list_agent_turn_events(
    client: DBProxyClient,
    *,
    turn_id: str | None = None,
    conversation_id: str | None = None,
    after_sequence: int = 0,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Read canonical core events for reconnect recovery.

    Args:
        client: DB Proxy client instance.
        turn_id: Optional turn filter.
        conversation_id: Optional conversation filter.
        after_sequence: Return events after this sequence number.
        limit: Maximum events returned.

    Returns:
        Turn event rows.
    """
    return await client._send_list_operation(
        "list_agent_turn_events",
        result_key="events",
        parameters={
            "turn_id": turn_id,
            "conversation_id": conversation_id,
            "after_sequence": after_sequence,
            "limit": limit,
        },
    )


async def project_agent_turn_event(
    client: DBProxyClient,
    event: dict[str, Any],
) -> bool:
    """Idempotently write one core event into the runtime projection.

    Args:
        client: DB Proxy client instance.
        event: Core event to project.

    Returns:
        True if a projection row was written or updated.
    """
    result = await client.send_operation("project_agent_turn_event", event)
    return bool(result.get("projected"))


async def mark_agent_turn_event_projected(
    client: DBProxyClient,
    event_id: str,
    projection_version: int = 1,
) -> bool:
    """Acknowledge one idempotent runtime projection.

    Args:
        client: DB Proxy client instance.
        event_id: Projected event ID.
        projection_version: Projection schema version.

    Returns:
        True if the source event was marked projected.
    """
    return await client._send_bool_operation(
        "mark_agent_turn_event_projected",
        result_key="updated",
        parameters={
            "event_id": event_id,
            "projection_version": projection_version,
        },
    )


async def recover_agent_turns(client: DBProxyClient) -> int:
    """Fence invocation claims abandoned by a previous orchestrator.

    Args:
        client: DB Proxy client instance.

    Returns:
        Number of fenced claims.
    """
    result = await client.send_operation("recover_agent_turns", {})
    return int(result.get("recovered", 0))


async def create_agent_clarification(
    client: DBProxyClient,
    *,
    turn_id: str,
    clarification_id: str,
    proposed_tool: str,
    reason: str,
    expires_at: str,
) -> dict[str, Any]:
    """Persist one backend-owned low-confidence proposal.

    Args:
        client: DB Proxy client instance.
        turn_id: Turn the clarification belongs to.
        clarification_id: Stable clarification identifier.
        proposed_tool: Tool the assistant is unsure about.
        reason: Why the clarification is needed.
        expires_at: Expiry time (ISO-8601).

    Returns:
        Clarification result.
    """
    return await client.send_operation(
        "create_agent_clarification",
        {
            "turn_id": turn_id,
            "clarification_id": clarification_id,
            "proposed_tool": proposed_tool,
            "reason": reason,
            "expires_at": expires_at,
        },
    )


async def get_pending_agent_clarification(
    client: DBProxyClient,
    conversation_id: str,
) -> dict[str, Any] | None:
    """Read the latest live proposal for one conversation.

    Args:
        client: DB Proxy client instance.
        conversation_id: Conversation to query.

    Returns:
        Clarification dict, or None if no live proposal exists.
    """
    return await client._send_optional_dict_operation(
        "get_pending_agent_clarification",
        result_key="clarification",
        parameters={"conversation_id": conversation_id},
    )


async def decide_agent_clarification(
    client: DBProxyClient,
    clarification_id: str,
    decision: str,
) -> dict[str, Any]:
    """Close a proposal with a classified decision.

    Args:
        client: DB Proxy client instance.
        clarification_id: Clarification to close.
        decision: Classified decision value.

    Returns:
        Decision result.
    """
    return await client.send_operation(
        "decide_agent_clarification",
        {"clarification_id": clarification_id, "decision": decision},
    )


async def create_agent_pending_input(
    client: DBProxyClient,
    *,
    turn_id: str,
    pending_input_id: str,
    question: str,
    suggestions: list[str],
    expected_input_schema: dict[str, object],
    transcript_snapshot: list[dict[str, object]],
    expires_at: str,
) -> dict[str, object]:
    """Persist one typed missing-input pause in core authority.

    Args:
        client: DB Proxy client instance.
        turn_id: Turn the pause belongs to.
        pending_input_id: Stable pending input identifier.
        question: Question asked of the user.
        suggestions: Suggested answers.
        expected_input_schema: Expected answer schema.
        transcript_snapshot: Transcript at pause time.
        expires_at: Expiry time (ISO-8601).

    Returns:
        Persisted pending input state.
    """
    result = await client.send_operation(
        "create_agent_pending_input",
        {
            "turn_id": turn_id,
            "pending_input_id": pending_input_id,
            "question": question,
            "suggestions": suggestions,
            "expected_input_schema": expected_input_schema,
            "transcript_snapshot": transcript_snapshot,
            "expires_at": expires_at,
        },
    )
    return dict(result.get("pending_input") or {})


async def get_agent_pending_input(
    client: DBProxyClient,
    *,
    pending_input_id: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, object] | None:
    """Read one typed missing-input state from core authority.

    Args:
        client: DB Proxy client instance.
        pending_input_id: Pending input to read, if known.
        conversation_id: Conversation to read, if the ID is unknown.

    Returns:
        Pending input state, or None if none exists.
    """
    result = await client.send_operation(
        "get_agent_pending_input",
        {
            "pending_input_id": pending_input_id,
            "conversation_id": conversation_id,
        },
    )
    value = result.get("pending_input")
    return dict(value) if isinstance(value, dict) else None


async def reply_agent_pending_input(
    client: DBProxyClient,
    *,
    turn_id: str,
    pending_input_id: str,
    conversation_id: str,
    cas_version: int,
    value: object,
) -> dict[str, object]:
    """Validate and CAS one reply into the same structured run.

    Args:
        client: DB Proxy client instance.
        turn_id: Turn receiving the reply.
        pending_input_id: Pending input being answered.
        conversation_id: Conversation of the pending input.
        cas_version: CAS version of the pending input state.
        value: User answer to validate.

    Returns:
        Updated pending input state, with validation_error set on
        validation failure.
    """
    result = await client.send_operation(
        "reply_agent_pending_input",
        {
            "turn_id": turn_id,
            "pending_input_id": pending_input_id,
            "conversation_id": conversation_id,
            "cas_version": cas_version,
            "value": value,
        },
    )
    pending = dict(result.get("pending_input") or {})
    if result.get("validation_error") is not None:
        pending["validation_error"] = str(result["validation_error"])
    return pending
