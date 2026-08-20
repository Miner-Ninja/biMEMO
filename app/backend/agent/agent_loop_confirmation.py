"""Structured confirmation and authority-turn state machine.

Provides module-level functions for the core-authority
confirmation decision/resume flow, the structured authority turn
(native tool rounds, evidence gate, synthesis, commit), and the
interactive outcome commit machinery. Each function receives an
AgentOrchestrator instance as its first parameter.
"""

from __future__ import annotations

import inspect
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import TYPE_CHECKING

from backend.agent.agent_loop_helpers import (
    build_assistant_message_meta,
    build_dialog_step_record_input,
)
from backend.agent.agent_loop_models import (
    AgentResponse,
    PreparedContext,
    TurnPreparation,
)
from backend.agent.finalization import (
    RequirementSatisfaction,
    ResponseRequirements,
)
from backend.agent.llm_caller import LLMStreamMode
from backend.agent.loop import AgenticLoop
from backend.agent.semantic_planning import (
    SemanticDecisionKind,
    SemanticProtocolError,
    parse_semantic_decision,
    with_semantic_controls,
)
from backend.agent.stage_timings import humanize_stage_label
from backend.agent.structured_finalization import (
    StructuredFinalizationError,
    StructuredFinalizer,
)
from backend.agent.turn_state import (
    ConfirmationStatus,
    InvocationStatus,
    ObligationStatus,
    PendingInput,
    PendingInputStatus,
    TerminalError,
    ToolObligation,
    TurnOutcome,
    TurnOutcomeKind,
)
from backend.agent.turn_state import (
    PendingConfirmation as TurnPendingConfirmation,
)
from backend.constants import TOOL_POLICY_VERSION
from backend.llm.capabilities import CapabilitySupport
from backend.llm.structured_response import StructuredAssistantContent
from backend.llm.usage import aggregate_call_usages
from backend.memory.classification.question_router import route_memory_question
from backend.permissions import ActionContext, DecisionKind
from backend.processes.orchestrator.run_event_recorder import (
    AgentRunEventType,
)
from backend.security.content_filter import filter_output
from backend.security.policy import get_policy
from backend.security.provenance import serialize_tool_result
from backend.tools.broker import AuthorizationContext, ToolBroker


if TYPE_CHECKING:
    from backend.agent.agent_loop import AgentOrchestrator
    from backend.llm.capabilities import ModelCapabilities
    from backend.llm.usage import LLMCallUsage


logger = logging.getLogger(__name__)


def _executed_tools_from_canonical_invocations(
    canonical: dict[str, object],
) -> list[dict]:
    """Rebuild the executed-tools trace from durable turn invocations.

    A confirmation batch with more than one card resolves across several
    separate ``resume_structured_confirmation`` calls, each a fresh
    ``ToolExecutor`` with no memory of what an earlier call already ran. The
    canonical turn record is the only thing that survives all of them, so
    finished invocations are replayed from there instead of starting empty.
    """
    invocations = canonical.get("invocations", [])
    if not isinstance(invocations, list):
        return []
    executed: list[dict] = []
    for item in invocations:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", ""))
        if status not in ("succeeded", "failed"):
            continue
        raw_result = item.get("result")
        result: dict = raw_result if isinstance(raw_result, dict) else {}
        executed.append(
            {
                "id": str(item.get("tool_call_id", "")),
                "name": str(item.get("tool_name", "")),
                "parameters": item.get("arguments", {}),
                "permission_mode": str(result.get("permission_mode", "unknown")),
                "authorization_source": str(
                    result.get("authorization_source", item.get("source", ""))
                ),
                "duration_ms": result.get("duration_ms"),
                "status": status,
                "success": status == "succeeded",
            }
        )
    return executed


def _semantic_tool_choice(
    *,
    target: ToolObligation | None,
    canonical: dict[str, object],
    max_rounds: int,
) -> dict[str, object] | str:
    """Force obligations and bound optional evidence collection."""
    if target is not None:
        return {
            "type": "function",
            "function": {"name": target.required_tool},
        }
    successful_evidence = sum(
        isinstance(item, dict) and item.get("status") == "succeeded"
        for item in canonical.get("invocations", [])
    )
    # One follow-up tool decision preserves useful chains such as
    # search -> fetch. Afterwards require an ordinary final answer.
    if successful_evidence >= 2:
        return "none"
    return "auto"


async def resume_structured_confirmation(
    orchestrator: AgentOrchestrator,
    *,
    confirmation_id: str,
    decision: str,
    llm_call: object,
    scope: str = "once",
    preliminary_call_usages: list[LLMCallUsage] | None = None,
) -> AgentResponse:
    """Decide and resume one core-authority confirmation continuation.

    ``scope`` applies to approvals only: "once" executes without any
    reusable grant; "conversation" keeps the policy-scoped reusable
    grant; "domain" binds the grant to the host of the invocation's
    canonical URL argument. Policy remains the ceiling — a scope wider
    than the tool's permission mode is REFUSED with a typed failure
    (scope_exceeds_policy), never silently degraded.
    """
    if scope not in {"once", "domain", "conversation"}:
        raise ValueError(f"Unknown confirmation scope: {scope!r}")
    active_started_at = perf_counter()
    if orchestrator.turn_store is None:
        raise RuntimeError("structured confirmation has no core authority store")
    confirmation = await orchestrator.turn_store.get_agent_confirmation(confirmation_id)
    if not isinstance(confirmation, dict):
        raise RuntimeError("Unknown confirmation identity")
    turn_id = str(confirmation.get("turn_id", ""))
    invocation_id = str(confirmation.get("invocation_id", ""))
    canonical = await orchestrator.turn_store.get_agent_turn(turn_id)
    if canonical is None:
        raise RuntimeError("authoritative confirmation turn disappeared")
    replay = orchestrator._agent_response_from_terminal_turn(canonical)
    if replay is not None:
        return replay

    await orchestrator.concurrency.acquire()
    orchestrator._current_run_id = turn_id
    try:
        orchestrator._provider_attempt_tasks = []
        orchestrator.tool_executor.clear_pending()
        already_executed = _executed_tools_from_canonical_invocations(canonical)
        orchestrator.tool_executor.tool_call_count = len(already_executed)
        orchestrator.tool_executor.tool_names_used = [
            str(item["name"]) for item in already_executed
        ]
        orchestrator.tool_executor.executed_tools = already_executed
        orchestrator.tool_executor.current_context = {
            "conversation_id": str(canonical.get("conversation_id", "")),
            "session_id": str(canonical.get("session_id", "")),
            "channel": str(canonical.get("channel", "")),
            "agent_run_id": turn_id,
            "tool_policy_version": str(canonical.get("tool_policy_version", "")),
        }
        prepared = orchestrator._restore_structured_preparation(
            canonical, invocation_id
        )
        prepared.call_usages.extend(preliminary_call_usages or [])
        loop = AgenticLoop(injection_flag=False)
        loop.start()
        pending_confirmations = [
            item
            for item in canonical.get("confirmations", [])
            if item.get("status") == "pending"
        ]
        canonical_decision = confirmation
        if decision == ConfirmationStatus.DENIED:
            canonical_decision = (
                await orchestrator.turn_store.decide_agent_confirmation(
                    confirmation_id,
                    decision,
                )
            )
            if canonical_decision.get("changed") and isinstance(
                orchestrator.tool_executor.broker, ToolBroker
            ):
                tool_name = str(confirmation.get("tool_name", ""))
                arguments = dict(confirmation.get("parameters", {}))
                denied_prepared = orchestrator.tool_executor.broker.prepare(
                    tool_name,
                    arguments,
                )
                denied_policy = orchestrator.tool_executor.action_decisions.decide(
                    ActionContext(
                        conversation_id=str(canonical.get("conversation_id", "")),
                        channel=str(canonical.get("channel", "")),
                        session_id=str(canonical.get("session_id", "")),
                        tool_name=tool_name,
                        parameters=arguments,
                        tool_policy_version=str(
                            canonical.get("tool_policy_version", "")
                        ),
                        prepared=denied_prepared,
                    )
                )
                orchestrator.tool_executor.broker.record_authorization_outcome(
                    prepared=denied_prepared,
                    authorization=AuthorizationContext(
                        permission_mode=str(
                            denied_policy.details.get(
                                "permission_mode", "per_invocation"
                            )
                        ),
                        policy_revision=str(
                            denied_policy.details.get(
                                "policy_revision", TOOL_POLICY_VERSION
                            )
                        ),
                        source="user_confirmation",
                        authorization_id=confirmation_id,
                    ),
                    outcome="tool_denied",
                    reason="User denied the requested tool action",
                    correlation={
                        "agent_run_id": turn_id,
                        "invocation_id": invocation_id,
                        "conversation_id": str(canonical.get("conversation_id", "")),
                        "session_id": str(canonical.get("session_id", "")),
                        "scheduled_task_id": "",
                    },
                )
            refreshed = await orchestrator.turn_store.get_agent_turn(turn_id)
            if refreshed is None:
                raise RuntimeError(
                    "authoritative confirmation turn disappeared after decision"
                )
            canonical = refreshed
            pending_confirmations = [
                item
                for item in canonical.get("confirmations", [])
                if item.get("status") == "pending"
            ]
        if canonical_decision.get("status") == ConfirmationStatus.EXPIRED:
            if pending_confirmations:
                return orchestrator._paused_response_from_canonical(canonical, loop)
            return await orchestrator._commit_structured_failure(
                prepared_turn=prepared,
                session_id=str(canonical.get("session_id", "")),
                conversation_id=str(canonical.get("conversation_id", "")),
                loop=loop,
                code="tool_confirmation_expired",
                message="The requested action expired before it was approved.",
            )
        if decision == ConfirmationStatus.DENIED and not canonical_decision.get(
            "changed"
        ):
            invocation_status = next(
                (
                    str(item.get("status", ""))
                    for item in canonical.get("invocations", [])
                    if item.get("invocation_id") == invocation_id
                ),
                "",
            )
            pending = [
                item
                for item in canonical.get("confirmations", [])
                if item.get("status") == "pending"
            ]
            if pending:
                return orchestrator._paused_response_from_canonical(canonical, loop)
            if invocation_status == "succeeded":
                return await orchestrator._run_structured_authority_turn(
                    prepared_turn=prepared,
                    session_id=str(canonical.get("session_id", "")),
                    conversation_id=str(canonical.get("conversation_id", "")),
                    loop=loop,
                    llm_call=llm_call,
                    channel=str(canonical.get("channel", "")),
                )
            raise RuntimeError(
                f"confirmation is already {canonical_decision.get('status', 'decided')}"
            )
        if decision == ConfirmationStatus.DENIED:
            if pending_confirmations:
                return orchestrator._paused_response_from_canonical(canonical, loop)
            return await orchestrator._commit_structured_failure(
                prepared_turn=prepared,
                session_id=str(canonical.get("session_id", "")),
                conversation_id=str(canonical.get("conversation_id", "")),
                loop=loop,
                code="tool_confirmation_denied",
                message=(
                    "The requested action was denied, so the turn could not "
                    "be completed."
                ),
            )

        active_timeout_ms = (
            get_policy().get_int("agent_limits.loop_timeout_seconds") * 1000
        )
        live_resume_ms = max(
            0,
            int((perf_counter() - active_started_at) * 1000),
        )
        if (
            int(canonical.get("active_time_ms", 0)) + live_resume_ms
            >= active_timeout_ms
        ):
            return await orchestrator._commit_structured_failure(
                prepared_turn=prepared,
                session_id=str(canonical.get("session_id", "")),
                conversation_id=str(canonical.get("conversation_id", "")),
                loop=loop,
                code="active_deadline_exhausted",
                message="The active execution deadline was exhausted.",
            )

        invocation = next(
            (
                item
                for item in canonical.get("invocations", [])
                if item.get("invocation_id") == invocation_id
            ),
            None,
        )
        if not isinstance(invocation, dict):
            raise RuntimeError("confirmed invocation is missing")
        invocation_tool = str(invocation.get("tool_name", ""))
        invocation_arguments = dict(invocation.get("arguments", {}))
        prepared_invocation = orchestrator.tool_executor.broker.restore_prepared(
            invocation_tool,
            invocation_arguments,
            expected_hash=str(invocation.get("canonical_args_hash", "")),
            expected_policy_revision=str(invocation.get("policy_revision", "")),
        )
        permission = orchestrator.tool_executor.action_decisions.decide(
            ActionContext(
                conversation_id=str(canonical.get("conversation_id", "")),
                channel=str(canonical.get("channel", "")),
                session_id=str(canonical.get("session_id", "")),
                tool_name=invocation_tool,
                parameters=invocation_arguments,
                tool_policy_version=str(canonical.get("tool_policy_version", "")),
                prepared=prepared_invocation,
            )
        )
        if permission.kind == DecisionKind.BLOCK:
            return await orchestrator._commit_structured_failure(
                prepared_turn=prepared,
                session_id=str(canonical.get("session_id", "")),
                conversation_id=str(canonical.get("conversation_id", "")),
                loop=loop,
                code="tool_authorization_blocked",
                message="The approved action is blocked by the current policy.",
            )
        grant_capable = str(permission.details.get("permission_mode", "")) in {
            "conversation_capability",
            "scoped",
        }
        # Policy is the ceiling (contract S5.3/S6.4): a scope wider than
        # the tool's permission mode is REFUSED with a typed failure —
        # never silently degraded — so a raw HTTP client bypassing the
        # panel gains nothing.
        if scope in {"domain", "conversation"} and not grant_capable:
            return await orchestrator._commit_structured_failure(
                prepared_turn=prepared,
                session_id=str(canonical.get("session_id", "")),
                conversation_id=str(canonical.get("conversation_id", "")),
                loop=loop,
                code="scope_exceeds_policy",
                message=(
                    "This tool requires a separate confirmation for "
                    "every call; a reusable approval is not available."
                ),
            )
        domain_host: str | None = None
        if scope == "domain":
            domain_host = orchestrator.tool_executor.canonical_url_host(
                prepared_invocation
            )
            if domain_host is None:
                return await orchestrator._commit_structured_failure(
                    prepared_turn=prepared,
                    session_id=str(canonical.get("session_id", "")),
                    conversation_id=str(canonical.get("conversation_id", "")),
                    loop=loop,
                    code="domain_scope_unavailable",
                    message=(
                        "Domain scope is only available for tools with a URL argument."
                    ),
                )
        coordinates = orchestrator.tool_executor._grant_coordinates(
            permission,
            domain_host=domain_host,
        )
        reusable = scope in {"domain", "conversation"}
        claim = await orchestrator.turn_store.authorize_agent_invocation(
            turn_id=turn_id,
            invocation_id=invocation_id,
            idempotency_key=f"{turn_id}:{invocation_id}",
            capability=str(invocation.get("tool_name", "")),
            channel=str(canonical.get("channel", "")),
            scope_type=str(coordinates["scope_type"]),
            scope_value=str(coordinates["scope_value"]),
            scope_json=dict(coordinates["scope"]),
            scope_hash=str(coordinates["scope_hash"]),
            policy_revision=str(coordinates["policy_revision"]),
            canonical_args_hash=prepared_invocation.canonical_hash,
            confirmation_id=confirmation_id,
            create_reusable_grant=reusable,
            grant_expires_at=(
                (
                    datetime.now(UTC)
                    + timedelta(
                        days=get_policy().get_int("trust.capability_grant_ttl_days")
                    )
                ).isoformat()
                if reusable
                else None
            ),
        )
        if not claim.get("claimed"):
            raise RuntimeError("confirmed invocation was already dispatched")
        generation = int(claim["claim_generation"])
        orchestrator.broker.set_context(
            {
                **orchestrator.tool_executor.current_context,
                "invocation_id": invocation_id,
                # The fetch that MINTS a domain grant must obey the same
                # host containment as later reuses of that grant.
                **({"domain_grant_host": domain_host} if domain_host else {}),
            }
        )
        authorization = AuthorizationContext(
            permission_mode=str(
                permission.details.get("permission_mode", "per_invocation")
            ),
            policy_revision=str(
                permission.details.get("policy_revision", TOOL_POLICY_VERSION)
            ),
            source="user_confirmation",
            authorization_id=confirmation_id,
        )
        resume_execution_started_at = perf_counter()
        try:
            result = await orchestrator.broker.execute_prepared(
                prepared_invocation,
                authorization,
                correlation={
                    **orchestrator.tool_executor.current_context,
                    "invocation_id": invocation_id,
                },
            )
        except Exception as exc:
            # No ToolResult was returned, so there is no result.duration_ms
            # to record — measure the wall clock locally, same as the
            # non-confirmed execution path in ToolExecutor.
            duration_ms = (perf_counter() - resume_execution_started_at) * 1000
            orchestrator._stage_timings.record(
                humanize_stage_label(invocation_tool), duration_ms
            )
            orchestrator.tool_executor.executed_tools.append(
                {
                    "id": invocation_id,
                    "name": invocation_tool,
                    "parameters": invocation_arguments,
                    "permission_mode": str(
                        permission.details.get("permission_mode", "")
                    ),
                    "authorization_source": "user_confirmation",
                    "duration_ms": duration_ms,
                    "status": "failed",
                    "success": False,
                    "error": str(exc),
                }
            )
            await orchestrator.turn_store.complete_agent_invocation(
                turn_id=turn_id,
                invocation_id=invocation_id,
                claim_generation=generation,
                status="failed",
                result={"duration_ms": duration_ms},
                error=str(exc),
            )
            return await orchestrator._commit_structured_failure(
                prepared_turn=prepared,
                session_id=str(canonical.get("session_id", "")),
                conversation_id=str(canonical.get("conversation_id", "")),
                loop=loop,
                code="tool_execution_failed",
                message="The approved action failed during execution.",
            )
        finally:
            orchestrator.broker.clear_context()
        success = bool(getattr(result, "success", False))
        result_data = orchestrator._json_safe(getattr(result, "result", None))
        envelope = json.loads(
            serialize_tool_result(
                result_data,
                tool_name=str(invocation.get("tool_name", "")),
                invocation_id=invocation_id,
            )
        )
        if success and envelope["truncated"]:
            success = False
            result_data = None
        resume_permission_mode = str(
            getattr(
                getattr(result, "authorization", None),
                "permission_mode",
                "unspecified",
            )
        )
        resume_authorization_source = str(
            getattr(
                getattr(result, "authorization", None),
                "source",
                "unspecified",
            )
        )
        resume_duration_ms = float(getattr(result, "duration_ms", 0))
        orchestrator._stage_timings.record(
            humanize_stage_label(str(invocation.get("tool_name", ""))),
            resume_duration_ms,
        )
        orchestrator.tool_executor.executed_tools.append(
            {
                "id": invocation_id,
                "name": str(invocation.get("tool_name", "")),
                "parameters": invocation_arguments,
                "permission_mode": resume_permission_mode,
                "authorization_source": resume_authorization_source,
                "duration_ms": resume_duration_ms,
                "status": "succeeded" if success else "failed",
                "success": success,
            }
        )
        await orchestrator.turn_store.complete_agent_invocation(
            turn_id=turn_id,
            invocation_id=invocation_id,
            claim_generation=generation,
            status="succeeded" if success else "failed",
            result={
                "data": result_data if success else None,
                "duration_ms": resume_duration_ms,
                "permission_mode": resume_permission_mode,
                "authorization_source": resume_authorization_source,
            },
            error=None
            if success
            else (
                "Mandatory tool result exceeded the synthesis evidence limit."
                if envelope["truncated"]
                else str(getattr(result, "result", "tool failed"))
            ),
        )
        if not success:
            return await orchestrator._commit_structured_failure(
                prepared_turn=prepared,
                session_id=str(canonical.get("session_id", "")),
                conversation_id=str(canonical.get("conversation_id", "")),
                loop=loop,
                code="tool_execution_failed",
                message="The approved action did not complete successfully.",
            )
        canonical = await orchestrator.turn_store.get_agent_turn(turn_id)
        if canonical is None:
            raise RuntimeError("authoritative turn disappeared after execution")
        pending = [
            item
            for item in canonical.get("confirmations", [])
            if item.get("status") == "pending"
        ]
        if pending:
            return orchestrator._paused_response_from_canonical(canonical, loop)
        prepared = orchestrator._restore_structured_preparation(
            canonical, invocation_id
        )
        return await orchestrator._run_structured_authority_turn(
            prepared_turn=prepared,
            session_id=str(canonical.get("session_id", "")),
            conversation_id=str(canonical.get("conversation_id", "")),
            loop=loop,
            llm_call=llm_call,
            channel=str(canonical.get("channel", "")),
        )
    finally:
        await orchestrator._flush_provider_attempts_logged()
        try:
            await orchestrator.turn_store.append_agent_turn_event(
                turn_id=turn_id,
                event_type=AgentRunEventType.ACTIVE_TIME_RECORDED.value,
                phase="foreground_resume",
                payload={
                    "duration_ms": max(
                        0,
                        int((perf_counter() - active_started_at) * 1000),
                    )
                },
            )
        except Exception:
            logger.exception("Failed to persist confirmation resume active time")
        orchestrator.concurrency.release()
        orchestrator._current_run_id = ""


def _json_safe(value: object) -> object:
    """Normalize an arbitrary tool result into JSON-compatible data."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def restore_structured_preparation(
    orchestrator: AgentOrchestrator,
    canonical: dict[str, object],
    invocation_id: str,
) -> TurnPreparation:
    """Rebuild a structured turn from its canonical batch transcript."""
    invocations = [
        item for item in canonical.get("invocations", []) if isinstance(item, dict)
    ]
    selected = next(
        (item for item in invocations if item.get("invocation_id") == invocation_id),
        invocations[-1] if invocations else None,
    )
    batch_id = str((selected or {}).get("batch_id", ""))
    batches = [item for item in canonical.get("batches", []) if isinstance(item, dict)]
    batch = next(
        (item for item in batches if item.get("batch_id") == batch_id),
        batches[-1] if batches else None,
    )
    if not isinstance(batch, dict):
        raise RuntimeError("confirmation resume transcript is missing")
    messages = list(batch.get("transcript_snapshot", []))
    assistant_message = batch.get("assistant_message")
    if not isinstance(assistant_message, dict):
        raise RuntimeError("confirmation assistant batch is malformed")
    messages.append(assistant_message)
    siblings = [item for item in invocations if item.get("batch_id") == batch_id]
    for sibling in siblings:
        status = str(sibling.get("status", ""))
        if status in {
            InvocationStatus.REQUIRED,
            InvocationStatus.PENDING_CONFIRMATION,
            InvocationStatus.EXECUTING,
        }:
            continue
        payload = (
            sibling.get("result")
            if status == "succeeded"
            else {
                "error": str(sibling.get("error") or status),
                "status": status,
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": str(sibling.get("tool_call_id", "")),
                "content": serialize_tool_result(
                    tool_name=str(sibling.get("tool_name", "tool")),
                    invocation_id=str(sibling.get("invocation_id", "")),
                    data=payload,
                ),
            }
        )
    obligations = tuple(
        ToolObligation(
            run_id=str(canonical.get("turn_id", "")),
            obligation_id=str(item.get("obligation_id", "")),
            required_tool=str(item.get("required_tool", "")),
            source=str(item.get("source", "")),
            evidence_key=str(item.get("evidence_key", "")),
            argument_predicate=item.get("argument_predicate"),
            status=ObligationStatus(str(item.get("status", "open"))),
        )
        for item in canonical.get("obligations", [])
        if isinstance(item, dict)
    )
    required_names = tuple(dict.fromkeys(item.required_tool for item in obligations))
    filtered_message = orchestrator._last_user_text(messages)
    tools = orchestrator.prompt_builder.select_openai_tools(
        filtered_message,
        required_names=set(required_names),
        semantic_selection=True,
    )
    tools = with_semantic_controls(tools)
    return TurnPreparation(
        filtered_message=filtered_message,
        input_filtered=False,
        request_started_at=perf_counter(),
        call_usages=[],
        prepared_context=PreparedContext(
            entries=[],
            retrieved_memories=[],
            retrieved_messages=None,
            memory_guidance=None,
        ),
        user_message_id=None,
        messages=messages,
        openai_tools=tools,
        question_route=route_memory_question(""),
        requirements=ResponseRequirements(
            required_tools=required_names,
            must_use_tools=required_names,
        ),
        satisfaction=RequirementSatisfaction(),
        run_id=str(canonical.get("turn_id", "")),
        finalization_id=str(canonical.get("finalization_id", "")),
        obligations=obligations,
    )


def _last_user_text(messages: list[dict[str, object]]) -> str:
    """Extract a conservative text hint from the latest user message."""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
    return ""


def _paused_response_from_canonical(
    orchestrator: AgentOrchestrator,
    canonical: dict[str, object],
    loop: AgenticLoop,
) -> AgentResponse:
    """Build PAUSED from canonical confirmation rows after a sibling decision."""
    confirmations = tuple(
        TurnPendingConfirmation(
            run_id=str(canonical.get("turn_id", "")),
            confirmation_id=str(item.get("confirmation_id", "")),
            invocation_id=str(item.get("invocation_id", "")),
            status=ConfirmationStatus.PENDING,
            expires_at=str(item.get("expires_at", "")),
            title=str(item.get("title", "")),
            description=str(item.get("description", "")),
            tool_name=str(item.get("tool_name", "")),
            parameters=dict(item.get("parameters", {})),
        )
        for item in canonical.get("confirmations", [])
        if isinstance(item, dict) and item.get("status") == "pending"
    )
    outcome = TurnOutcome(
        kind=TurnOutcomeKind.PAUSED,
        run_id=str(canonical.get("turn_id", "")),
        confirmations=confirmations,
    )
    return AgentResponse(
        structured_content=None,
        loop_result=loop.complete(""),
        outcome=outcome,
        run_id=str(canonical.get("turn_id", "")),
    )


def _agent_response_from_terminal_turn(
    canonical: dict[str, object],
) -> AgentResponse | None:
    """Return a replay response when a confirmation targets a terminal turn."""
    terminal = str(canonical.get("terminal_outcome") or "")
    if terminal not in {TurnOutcomeKind.FINAL, TurnOutcomeKind.FAILED}:
        return None
    payload = canonical.get("outcome_payload")
    data = payload if isinstance(payload, dict) else {}
    raw_content = data.get("structured_content")
    structured = (
        StructuredAssistantContent(
            answer=str(raw_content.get("answer", "")),
            thinking=(
                str(raw_content["thinking"])
                if raw_content.get("thinking") is not None
                else None
            ),
            suggestions=[str(item) for item in raw_content.get("suggestions", [])],
        )
        if isinstance(raw_content, dict)
        else StructuredAssistantContent(answer="The turn is already complete.")
    )
    raw_error = data.get("error")
    error = (
        TerminalError(
            code=str(raw_error.get("code", "failed")),
            message=str(raw_error.get("message", structured.answer)),
            retryable=bool(raw_error.get("retryable", False)),
        )
        if isinstance(raw_error, dict)
        else None
    )
    kind = TurnOutcomeKind(terminal)
    raw_used_tools = data.get("used_tools")
    used_tools = raw_used_tools if isinstance(raw_used_tools, list) else []
    outcome = TurnOutcome(
        kind=kind,
        run_id=str(canonical.get("turn_id", "")),
        finalization_id=str(canonical.get("finalization_id", "")),
        structured_content=structured,
        error=error,
        metadata={"used_tools": used_tools},
    )
    loop = AgenticLoop(injection_flag=False)
    loop.start()
    return AgentResponse(
        structured_content=structured,
        loop_result=loop.complete(structured.answer),
        outcome=outcome,
        run_id=outcome.run_id,
        finalization_id=outcome.finalization_id,
        used_tools=used_tools,
    )


async def run_structured_authority_turn(
    orchestrator: AgentOrchestrator,
    *,
    prepared_turn: TurnPreparation,
    session_id: str,
    conversation_id: str,
    loop: AgenticLoop,
    llm_call: object,
    channel: str,
) -> AgentResponse:
    """Run native action rounds, evidence gate, synthesis, and core commit."""
    if orchestrator.turn_store is None or prepared_turn.run_id is None:
        raise RuntimeError("structured turn has no core authority store")
    active_timeout_ms = get_policy().get_int("agent_limits.loop_timeout_seconds") * 1000

    def active_deadline_exhausted(canonical_turn: dict[str, object]) -> bool:
        current_segment_ms = max(
            0,
            int((perf_counter() - prepared_turn.request_started_at) * 1000),
        )
        return (
            int(canonical_turn.get("active_time_ms", 0)) + current_segment_ms
            >= active_timeout_ms
        )

    initial_canonical = await orchestrator.turn_store.get_agent_turn(
        prepared_turn.run_id
    )
    if initial_canonical is None:
        raise RuntimeError("authoritative turn disappeared")
    if active_deadline_exhausted(initial_canonical):
        return await orchestrator._commit_structured_failure(
            prepared_turn=prepared_turn,
            session_id=session_id,
            conversation_id=conversation_id,
            loop=loop,
            code="active_deadline_exhausted",
            message="The active execution deadline was exhausted.",
        )
    try:
        await orchestrator._require_structured_capabilities(
            stage="tool",
            exact_choice=bool(prepared_turn.obligations),
        )
    except RuntimeError as exc:
        return await orchestrator._commit_structured_failure(
            prepared_turn=prepared_turn,
            session_id=session_id,
            conversation_id=conversation_id,
            loop=loop,
            code="provider_capability_unavailable",
            message=str(exc),
        )
    orchestrator.tool_executor.current_obligations = prepared_turn.obligations
    max_rounds = get_policy().get_int("agent_limits.max_semantic_tool_rounds")

    finish_requested = False
    allowed_external_names = {
        str(item.get("function", {}).get("name", ""))
        for item in prepared_turn.openai_tools or []
        if isinstance(item, dict)
        and isinstance(item.get("function"), dict)
        and str(item.get("function", {}).get("name", "")) != "bimemo_ask_user"
    }
    for _round in range(max_rounds):
        canonical = await orchestrator.turn_store.get_agent_turn(prepared_turn.run_id)
        if canonical is None:
            raise RuntimeError("authoritative turn disappeared")
        if active_deadline_exhausted(canonical):
            return await orchestrator._commit_structured_failure(
                prepared_turn=prepared_turn,
                session_id=session_id,
                conversation_id=conversation_id,
                loop=loop,
                code="active_deadline_exhausted",
                message="The active execution deadline was exhausted.",
            )
        binds = {
            str(item.get("obligation_id", ""))
            for item in canonical.get("binds", [])
            if isinstance(item, dict)
        }
        open_obligations = [
            obligation
            for obligation in prepared_turn.obligations
            if obligation.obligation_id not in binds
        ]
        if int(canonical.get("tool_rounds", 0)) >= max_rounds:
            return await orchestrator._commit_structured_failure(
                prepared_turn=prepared_turn,
                session_id=session_id,
                conversation_id=conversation_id,
                loop=loop,
                code="evidence_budget_exhausted",
                message="Required tool evidence exceeded the durable call budget.",
            )
        target = open_obligations[0] if open_obligations else None
        forced_choice = _semantic_tool_choice(
            target=target,
            canonical=canonical,
            max_rounds=max_rounds,
        )
        await orchestrator.turn_store.append_agent_turn_event(
            turn_id=prepared_turn.run_id,
            event_type=AgentRunEventType.TOOL_ROUND_STARTED.value,
            phase="tool_loop",
            attempt=int(canonical.get("tool_rounds", 0)) + 1,
            payload={
                "obligation_id": target.obligation_id if target else None,
                "required_tool": target.required_tool if target else None,
            },
        )
        llm_response = await orchestrator.llm_caller.call_once(
            llm_call=llm_call,
            messages=prepared_turn.messages,
            openai_tools=prepared_turn.openai_tools,
            call_usages=prepared_turn.call_usages,
            tool_choice=forced_choice,
            stream_mode=LLMStreamMode.DISABLED,
        )
        await orchestrator._flush_provider_attempts()
        canonical = await orchestrator.turn_store.get_agent_turn(prepared_turn.run_id)
        if canonical is None:
            raise RuntimeError("authoritative turn disappeared after provider call")
        if active_deadline_exhausted(canonical):
            return await orchestrator._commit_structured_failure(
                prepared_turn=prepared_turn,
                session_id=session_id,
                conversation_id=conversation_id,
                loop=loop,
                code="active_deadline_exhausted",
                message="The active execution deadline was exhausted.",
            )
        try:
            semantic_decision = parse_semantic_decision(
                llm_response,
                allowed_external_names=allowed_external_names,
            )
        except SemanticProtocolError as exc:
            used_reask = int(canonical.get("enforcement_reasks", 0))
            if used_reask >= 1:
                return await orchestrator._commit_structured_failure(
                    prepared_turn=prepared_turn,
                    session_id=session_id,
                    conversation_id=conversation_id,
                    loop=loop,
                    code="native_decision_invalid",
                    message=(f"The model provider returned an invalid decision: {exc}"),
                )
            await orchestrator.turn_store.append_agent_turn_event(
                turn_id=prepared_turn.run_id,
                event_type=AgentRunEventType.NATIVE_DECISION_INVALID.value,
                phase="tool_loop",
                attempt=used_reask + 1,
                payload={
                    "obligation_id": target.obligation_id if target else None,
                    "required_tool": target.required_tool if target else None,
                    "error": str(exc),
                },
            )
            loop.advance()
            continue

        if semantic_decision.kind is SemanticDecisionKind.FINISH:
            if open_obligations:
                used_reask = int(canonical.get("enforcement_reasks", 0))
                if used_reask >= 1:
                    return await orchestrator._commit_structured_failure(
                        prepared_turn=prepared_turn,
                        session_id=session_id,
                        conversation_id=conversation_id,
                        loop=loop,
                        code="finish_with_open_obligation",
                        message=(
                            "The model tried to finish before required evidence "
                            "succeeded."
                        ),
                    )
                await orchestrator.turn_store.append_agent_turn_event(
                    turn_id=prepared_turn.run_id,
                    event_type=AgentRunEventType.FINISH_REJECTED.value,
                    phase="tool_loop",
                    attempt=used_reask + 1,
                    payload={
                        "open_obligations": [
                            item.obligation_id for item in open_obligations
                        ]
                    },
                )
                loop.advance()
                continue
            successful_evidence = any(
                isinstance(item, dict) and item.get("status") == "succeeded"
                for item in canonical.get("invocations", [])
            )
            if not successful_evidence:
                await orchestrator.turn_store.append_agent_turn_event(
                    turn_id=prepared_turn.run_id,
                    event_type=AgentRunEventType.SEMANTIC_FINISH_WITHOUT_TOOL.value,
                    phase="tool_loop",
                    attempt=int(canonical.get("tool_rounds", 0)),
                    payload={"model_owned_judgment": True},
                )
            prepared_turn.messages.append(
                {
                    "role": "assistant",
                    "content": semantic_decision.answer,
                }
            )
            finish_requested = True
            break

        if semantic_decision.kind is SemanticDecisionKind.ASK_USER:
            return await orchestrator._pause_for_missing_input(
                prepared_turn=prepared_turn,
                loop=loop,
                llm_call=llm_call,
                session_id=session_id,
                conversation_id=conversation_id,
                question=semantic_decision.question,
                expected_input_schema=semantic_decision.expected_input_schema or {},
                assistant_message=(
                    dict(llm_response.raw_message)
                    if isinstance(llm_response.raw_message, dict)
                    else {}
                ),
            )

        matching_forced_call = bool(
            target is None
            or any(
                call.name == target.required_tool
                for call in semantic_decision.external_calls
            )
        )
        if not matching_forced_call:
            used_reask = int(canonical.get("enforcement_reasks", 0))
            if used_reask >= 1:
                return await orchestrator._commit_structured_failure(
                    prepared_turn=prepared_turn,
                    session_id=session_id,
                    conversation_id=conversation_id,
                    loop=loop,
                    code="exact_tool_choice_ignored",
                    message=(
                        "The model provider did not honor the required tool call."
                    ),
                )
            await orchestrator.turn_store.append_agent_turn_event(
                turn_id=prepared_turn.run_id,
                event_type=AgentRunEventType.EXACT_FORCE_IGNORED.value,
                phase="tool_loop",
                attempt=used_reask + 1,
                payload={
                    "obligation_id": target.obligation_id if target else None,
                    "required_tool": target.required_tool if target else None,
                },
            )
            loop.advance()
            continue
        try:
            await orchestrator.tool_executor.run_one_tool_round(
                llm_response=llm_response,
                messages=prepared_turn.messages,
                channel=channel,
                required_tools=prepared_turn.requirements.required_tools,
            )
        except Exception as exc:
            return await orchestrator._commit_structured_failure(
                prepared_turn=prepared_turn,
                session_id=session_id,
                conversation_id=conversation_id,
                loop=loop,
                code="tool_execution_failed",
                message=f"A required tool could not be executed: {exc}",
            )
        if orchestrator.tool_executor.pending_red_calls:
            return orchestrator._build_paused_structured_response(
                prepared_turn=prepared_turn,
                loop=loop,
            )
        loop.advance()
    else:
        return await orchestrator._commit_structured_failure(
            prepared_turn=prepared_turn,
            session_id=session_id,
            conversation_id=conversation_id,
            loop=loop,
            code="evidence_budget_exhausted",
            message=(
                "Required tool evidence was not completed within the call budget."
            ),
        )

    if not finish_requested:
        return await orchestrator._commit_structured_failure(
            prepared_turn=prepared_turn,
            session_id=session_id,
            conversation_id=conversation_id,
            loop=loop,
            code="semantic_round_budget_exhausted",
            message="The semantic planning round budget was exhausted.",
        )

    canonical = await orchestrator.turn_store.get_agent_turn(prepared_turn.run_id)
    if canonical is None:
        raise RuntimeError("authoritative turn disappeared before synthesis")
    if active_deadline_exhausted(canonical):
        return await orchestrator._commit_structured_failure(
            prepared_turn=prepared_turn,
            session_id=session_id,
            conversation_id=conversation_id,
            loop=loop,
            code="active_deadline_exhausted",
            message="The active execution deadline was exhausted.",
        )
    bound_ids = {
        str(item.get("obligation_id", ""))
        for item in canonical.get("binds", [])
        if isinstance(item, dict)
    }
    missing = [
        item
        for item in prepared_turn.obligations
        if item.obligation_id not in bound_ids
    ]
    if missing:
        return await orchestrator._commit_structured_failure(
            prepared_turn=prepared_turn,
            session_id=session_id,
            conversation_id=conversation_id,
            loop=loop,
            code="missing_evidence",
            message="Required tool evidence is incomplete.",
        )
    try:
        capabilities = await orchestrator._require_structured_capabilities(
            stage="synthesis"
        )
        prior_synthesis_attempts = int(canonical.get("synthesis_attempts", 0))
        remaining_synthesis_attempts = 2 - prior_synthesis_attempts
        if remaining_synthesis_attempts <= 0:
            raise StructuredFinalizationError(
                "synthesis_budget_exhausted",
                "Structured synthesis exceeded the durable retry budget.",
            )
        finalizer = StructuredFinalizer(
            llm_call=llm_call,
            suggestions_enabled=orchestrator.prompt_builder.suggestions_enabled,
            on_attempt=orchestrator._record_provider_attempt,
            flush_attempts=orchestrator._flush_provider_attempts,
            max_context_tokens=int(capabilities.max_context_tokens),
            max_attempts=remaining_synthesis_attempts,
        )
        evidence_ledger = {
            "obligations": [
                {
                    "obligation_id": item.get("obligation_id"),
                    "required_tool": item.get("required_tool"),
                    "source": item.get("source"),
                    "evidence_key": item.get("evidence_key"),
                    "status": item.get("status"),
                }
                for item in canonical.get("obligations", [])
                if isinstance(item, dict)
            ],
            "binds": [
                {
                    "obligation_id": item.get("obligation_id"),
                    "invocation_id": item.get("invocation_id"),
                    "evidence_key": item.get("evidence_key"),
                }
                for item in canonical.get("binds", [])
                if isinstance(item, dict)
            ],
        }
        synthesis = await finalizer.finalize(
            [
                *prepared_turn.messages,
                {
                    "role": "system",
                    "content": (
                        "Backend-verified evidence ledger (JSON): "
                        + json.dumps(
                            evidence_ledger,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    ),
                },
            ],
            call_usages=prepared_turn.call_usages,
        )
    except (StructuredFinalizationError, RuntimeError) as exc:
        return await orchestrator._commit_structured_failure(
            prepared_turn=prepared_turn,
            session_id=session_id,
            conversation_id=conversation_id,
            loop=loop,
            code=getattr(exc, "code", "provider_capability_unavailable"),
            message=str(exc),
        )
    return await orchestrator._commit_structured_final(
        prepared_turn=prepared_turn,
        session_id=session_id,
        conversation_id=conversation_id,
        loop=loop,
        structured_content=synthesis.content,
        output_filtered=synthesis.output_filtered,
    )


async def require_structured_capabilities(
    orchestrator: AgentOrchestrator,
    *,
    stage: str,
    exact_choice: bool = False,
) -> ModelCapabilities:
    """Fail closed unless the active provider declares the required phase."""
    resolver = getattr(orchestrator, "_capabilities_resolver", None)
    if resolver is None:
        raise RuntimeError("Provider capability information is unavailable.")
    capabilities = resolver()
    if inspect.isawaitable(capabilities):
        capabilities = await capabilities
    required = ["non_streaming_custom_schema"]
    if stage == "tool":
        required = ["native_tools"]
        if exact_choice:
            required.extend(["required_native_decision", "exact_named_choice"])
    missing = [
        name
        for name in required
        if getattr(capabilities, name, CapabilitySupport.UNKNOWN)
        is not CapabilitySupport.SUPPORTED
    ]
    if missing:
        states = ", ".join(
            f"{name}={getattr(capabilities, name, CapabilitySupport.UNKNOWN).value}"
            for name in missing
        )
        raise RuntimeError(
            f"Provider capabilities do not permit structured {stage}: {states}."
        )
    return capabilities


async def pause_for_missing_input(
    orchestrator: AgentOrchestrator,
    *,
    prepared_turn: TurnPreparation,
    loop: AgenticLoop,
    llm_call: object,
    session_id: str,
    conversation_id: str,
    question: str,
    expected_input_schema: dict[str, object],
    assistant_message: dict[str, object],
) -> AgentResponse:
    """Persist one typed missing-input pause and return no final answer."""
    if orchestrator.turn_store is None or prepared_turn.run_id is None:
        raise RuntimeError("missing-input pause requires core turn authority")
    canonical = await orchestrator.turn_store.get_agent_turn(prepared_turn.run_id)
    round_number = int((canonical or {}).get("tool_rounds", 0))
    pending_input_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{prepared_turn.run_id}:pending-input:{round_number}:{question}",
        )
    )
    existing = next(
        (
            item
            for item in (canonical or {}).get("pending_inputs", [])
            if isinstance(item, dict)
            and item.get("pending_input_id") == pending_input_id
        ),
        None,
    )
    if existing is None:
        try:
            capabilities = await orchestrator._require_structured_capabilities(
                stage="synthesis"
            )
            formatter = StructuredFinalizer(
                llm_call=llm_call,
                suggestions_enabled=orchestrator.prompt_builder.suggestions_enabled,
                on_attempt=orchestrator._record_provider_attempt,
                flush_attempts=orchestrator._flush_provider_attempts,
                max_context_tokens=int(capabilities.max_context_tokens),
                max_attempts=2,
                phase="Pending input",
            )
            formatted = await formatter.finalize(
                [
                    *prepared_turn.messages,
                    {
                        "role": "system",
                        "content": (
                            "The assistant must pause for one missing value. "
                            "Format the following question as the complete "
                            "user-facing answer without answering the original "
                            "task. Preserve what value is requested."
                        ),
                    },
                    {"role": "assistant", "content": question},
                ],
                call_usages=prepared_turn.call_usages,
            )
        except (StructuredFinalizationError, RuntimeError) as exc:
            return await orchestrator._commit_structured_failure(
                prepared_turn=prepared_turn,
                session_id=session_id,
                conversation_id=conversation_id,
                loop=loop,
                code=getattr(exc, "code", "provider_capability_unavailable"),
                message=str(exc),
            )
        visible_question = formatted.content.answer
        suggestions = list(formatted.content.suggestions)
        expires_at = (datetime.now(UTC) + timedelta(hours=24)).isoformat()
    else:
        visible_question = str(existing.get("question", question))
        suggestions = [
            str(item)
            for item in existing.get("suggestions", [])
            if isinstance(item, str)
        ]
        expires_at = str(existing.get("expires_at", ""))
    stored = await orchestrator.turn_store.create_agent_pending_input(
        turn_id=prepared_turn.run_id,
        pending_input_id=pending_input_id,
        question=visible_question,
        suggestions=suggestions,
        expected_input_schema=expected_input_schema,
        transcript_snapshot=[*prepared_turn.messages, assistant_message],
        expires_at=expires_at,
    )
    pending_input = PendingInput(
        run_id=prepared_turn.run_id,
        pending_input_id=str(stored["pending_input_id"]),
        question=str(stored["question"]),
        expected_input_schema=dict(stored["expected_input_schema"]),
        status=PendingInputStatus(str(stored["status"])),
        expires_at=str(stored["expires_at"]),
        cas_version=int(stored["cas_version"]),
        suggestions=tuple(
            str(item) for item in stored.get("suggestions", []) if isinstance(item, str)
        ),
    )
    outcome = TurnOutcome(
        kind=TurnOutcomeKind.PAUSED,
        run_id=prepared_turn.run_id,
        pending_input=pending_input,
    )
    return AgentResponse(
        structured_content=None,
        loop_result=loop.complete(""),
        input_filtered=prepared_turn.input_filtered,
        retrieved_memories=prepared_turn.prepared_context.retrieved_memories,
        retrieved_messages=prepared_turn.prepared_context.retrieved_messages,
        used_tools=orchestrator.used_tools,
        outcome=outcome,
        run_id=prepared_turn.run_id,
    )


def _build_paused_structured_response(
    orchestrator: AgentOrchestrator,
    *,
    prepared_turn: TurnPreparation,
    loop: AgenticLoop,
) -> AgentResponse:
    """Return PAUSED without an assistant or dialog record."""
    confirmations = tuple(
        TurnPendingConfirmation(
            run_id=prepared_turn.run_id or "",
            confirmation_id=action.action_id,
            invocation_id=next(
                (
                    str(item.get("invocation_id", ""))
                    for item in orchestrator.tool_executor.executed_tools
                    if item.get("confirmation_id") == action.action_id
                ),
                "",
            ),
            status=ConfirmationStatus.PENDING,
            expires_at=(
                datetime.now(UTC)
                + timedelta(seconds=orchestrator.confirmation_mgr.timeout)
            ).isoformat(),
            title=action.tool_name,
            description=f"Allow {action.tool_name} with the proposed parameters",
            tool_name=action.tool_name,
            parameters=action.parameters,
        )
        for action in orchestrator.tool_executor.pending_red_calls
    )
    outcome = TurnOutcome(
        kind=TurnOutcomeKind.PAUSED,
        run_id=prepared_turn.run_id or "",
        confirmations=confirmations,
    )
    return AgentResponse(
        structured_content=None,
        loop_result=loop.complete(""),
        input_filtered=prepared_turn.input_filtered,
        retrieved_memories=prepared_turn.prepared_context.retrieved_memories,
        retrieved_messages=prepared_turn.prepared_context.retrieved_messages,
        used_tools=orchestrator.used_tools,
        outcome=outcome,
        run_id=prepared_turn.run_id,
    )


async def commit_structured_failure(
    orchestrator: AgentOrchestrator,
    *,
    prepared_turn: TurnPreparation,
    session_id: str,
    conversation_id: str,
    loop: AgenticLoop,
    code: str,
    message: str,
) -> AgentResponse:
    """Filter and atomically commit one interactive typed failure."""
    filtered = filter_output(message)
    safe_message = filtered.text.strip() or "The request could not be completed safely."
    content = StructuredAssistantContent(answer=safe_message)
    return await orchestrator._commit_core_interactive_outcome(
        prepared_turn=prepared_turn,
        session_id=session_id,
        conversation_id=conversation_id,
        loop=loop,
        kind=TurnOutcomeKind.FAILED,
        structured_content=content,
        output_filtered=bool(filtered.blocked or filtered.credentials_found),
        error=TerminalError(code=code, message=safe_message),
    )


async def commit_structured_final(
    orchestrator: AgentOrchestrator,
    *,
    prepared_turn: TurnPreparation,
    session_id: str,
    conversation_id: str,
    loop: AgenticLoop,
    structured_content: StructuredAssistantContent,
    output_filtered: bool,
) -> AgentResponse:
    """Atomically commit one accepted structured final."""
    return await orchestrator._commit_core_interactive_outcome(
        prepared_turn=prepared_turn,
        session_id=session_id,
        conversation_id=conversation_id,
        loop=loop,
        kind=TurnOutcomeKind.FINAL,
        structured_content=structured_content,
        output_filtered=output_filtered,
        error=None,
    )


async def _commit_core_interactive_outcome(
    orchestrator: AgentOrchestrator,
    *,
    prepared_turn: TurnPreparation,
    session_id: str,
    conversation_id: str,
    loop: AgenticLoop,
    kind: TurnOutcomeKind,
    structured_content: StructuredAssistantContent,
    output_filtered: bool,
    error: TerminalError | None,
) -> AgentResponse:
    """Commit message, dialog queue, turn, and canonical outcome together."""
    if (
        orchestrator.turn_store is None
        or prepared_turn.run_id is None
        or prepared_turn.finalization_id is None
    ):
        raise RuntimeError("structured final commit lacks durable identities")
    # The provider-attempt ledger is authoritative: a FINAL outcome may
    # only be committed once every attempt is durably stored. A flush
    # failure here surfaces before the commit, producing a clean typed
    # failure instead of losing an already-committed answer. FAILED
    # commits flush best-effort so a telemetry error cannot block
    # recording the failure itorchestrator.
    if kind is TurnOutcomeKind.FINAL:
        await orchestrator._flush_provider_attempts()
    else:
        await orchestrator._flush_provider_attempts_logged()
    stats = aggregate_call_usages(
        prepared_turn.call_usages,
        fallback_model=orchestrator.model_name,
        total_wall_time_ms=(perf_counter() - prepared_turn.request_started_at) * 1000,
        zone_usage=prepared_turn.prepared_context.zone_usage,
        stage_phases=orchestrator._stage_timings.phases,
    )
    structured_payload = {
        "answer": structured_content.answer,
        "thinking": None,
        "suggestions": structured_content.suggestions,
    }
    outcome_payload: dict[str, object] = {
        "structured_content": structured_payload,
        "input_filtered": prepared_turn.input_filtered,
        "output_filtered": output_filtered,
        "stats": stats.model_dump(mode="json") if stats is not None else None,
        "used_tools": orchestrator.used_tools,
    }
    if error is not None:
        outcome_payload["error"] = {
            "code": error.code,
            "message": error.message,
            "retryable": error.retryable,
        }
    source_ids = [
        value for value in (prepared_turn.user_message_id,) if value is not None
    ]
    task_result = None
    if (
        orchestrator.prompt_builder.scheduled_task_mode
        and orchestrator.scheduled_task_run
    ):
        started_at = str(orchestrator.scheduled_task_run["started_at"])
        finished_at = datetime.now(tz=UTC)
        started = datetime.fromisoformat(started_at)
        duration_ms = max(0, int((finished_at - started).total_seconds() * 1000))
        task_result = {
            "task_id": str(orchestrator.scheduled_task_run["task_id"]),
            "started_at": started_at,
            "finished_at": finished_at.isoformat(),
            "status": "ok" if kind is TurnOutcomeKind.FINAL else "error",
            "result_text": (
                structured_content.answer if kind is TurnOutcomeKind.FINAL else ""
            ),
            "error_message": (error.message[:1000] if error is not None else ""),
            "duration_ms": duration_ms,
        }
    committed = await orchestrator.turn_store.commit_agent_turn_finalization(
        turn_id=prepared_turn.run_id,
        finalization_id=prepared_turn.finalization_id,
        outcome_kind=kind.value,
        conversation_id=conversation_id,
        session_id=session_id,
        assistant_content=structured_content.answer,
        assistant_meta=build_assistant_message_meta(
            stats, structured_content, orchestrator.used_tools
        ),
        dialog_content=build_dialog_step_record_input(
            prepared_turn.filtered_message,
            structured_content.answer,
        ),
        source_message_ids=source_ids,
        outcome_payload=outcome_payload,
        persist_chat=(
            kind is TurnOutcomeKind.FINAL
            or not orchestrator.prompt_builder.scheduled_task_mode
        ),
        task_result=task_result,
    )
    raw_message_id = committed.get("assistant_message_id")
    assistant_message_id = str(raw_message_id) if raw_message_id else None
    outcome = TurnOutcome(
        kind=kind,
        run_id=prepared_turn.run_id,
        finalization_id=prepared_turn.finalization_id,
        structured_content=structured_content,
        error=error,
        assistant_message_id=assistant_message_id,
        metadata={"used_tools": orchestrator.used_tools},
    )
    return AgentResponse(
        structured_content=structured_content,
        loop_result=loop.complete(structured_content.answer),
        input_filtered=prepared_turn.input_filtered,
        output_filtered=output_filtered,
        retrieved_memories=prepared_turn.prepared_context.retrieved_memories,
        retrieved_messages=prepared_turn.prepared_context.retrieved_messages,
        stats=stats,
        assistant_message_id=assistant_message_id,
        used_tools=orchestrator.used_tools,
        outcome=outcome,
        run_id=prepared_turn.run_id,
        finalization_id=prepared_turn.finalization_id,
    )
