"""Task execution engine for the scheduler.

Handles executing both reminder (notification-only) and agent_task
(full agentic loop) scheduled tasks. Manages tool allowlist
filtering, conversation isolation, and result delivery via SSE.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from backend.agent.conversation_settings import read_suggestions_enabled
from backend.ipc.models import StreamEventType
from backend.processes.orchestrator.run_event_recorder import (
    RunEventContext,
    RunEventRecorder,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.agent.agent_loop import AgentOrchestrator
    from backend.processes.orchestrator.client import DBProxyClient
    from backend.tools.broker import ToolBroker


logger = logging.getLogger(__name__)


@dataclass
class SchedulerServices:
    """Service dependencies for the scheduler.

    Attributes:
        db_client: DB Proxy client for task operations.
        orchestrator: Dedicated AgentOrchestrator for scheduled tasks.
        llm_call: LLM call function.
        broker: Tool broker for schema lookup.
        sse_publish_fn: Callable to publish SSE events to connected clients.
        bridge_service: Optional ChatBridgeService for outbound delivery
            to messenger platforms (Telegram, Discord) linked to the
            originating conversation.
    """

    db_client: DBProxyClient
    orchestrator: AgentOrchestrator
    llm_call: Callable
    broker: ToolBroker
    sse_publish_fn: Callable | None = None
    bridge_service: object | None = None
    atomic_task_result_committed: bool = False


async def execute_task(
    services: SchedulerServices,
    task: dict[str, Any],
) -> tuple[str, bool]:
    """Execute a scheduled task.

    Args:
        services: Scheduler service dependencies.
        task: Task dict from the database.

    Returns:
        Tuple of (result_text, success).
    """
    services.atomic_task_result_committed = False
    task_type = task["task_type"]

    if task_type == "reminder":
        return await _execute_reminder(services, task)

    if task_type == "agent_task":
        return await _execute_agent_task(services, task)

    logger.error("Unknown task type: %s", task_type)
    return f"Unknown task type: {task_type}", False


async def _execute_reminder(
    services: SchedulerServices,
    task: dict[str, Any],
) -> tuple[str, bool]:
    """Execute a reminder task (notification only, no LLM)."""
    result_text = task["prompt"]
    await _emit_result_event(services, task, result_text, success=True)
    await _deliver_to_bridge(services, task, result_text)
    return result_text, True


async def _execute_agent_task(
    services: SchedulerServices,
    task: dict[str, Any],
) -> tuple[str, bool]:
    """Execute an agent task through a dedicated AgentOrchestrator."""
    task_id = task["id"]
    task_name = task["name"]
    prompt = task["prompt"]
    try:
        allowed_authorizations = _parse_allowed_tool_authorizations(
            task.get("allowed_tools", "[]"), services.broker
        )
    except ValueError as exc:
        task["_authorization_quarantined"] = True
        await services.db_client.update_scheduled_task(
            {"task_id": task_id, "status": "paused"}
        )
        return f"Scheduled tool authorization is invalid: {exc}", False
    allowed_tools = list(allowed_authorizations)

    # One stable conversation per task (bound to the task id) so every run's
    # result accumulates in the same chat instead of spawning a new one each run.
    conversation_id = f"sched_{task_id}"

    # Create it once; create_conversation is idempotent (INSERT OR IGNORE), so
    # repeated runs reuse the same conversation.
    await services.db_client.send_operation(
        "create_conversation",
        {"conversation_id": conversation_id, "title": f"[Scheduled] {task_name}"},
    )

    # Apply tool allowlist and scheduled-task system prompt
    # (restored in finally so the orchestrator is ready for next use)
    orch = services.orchestrator
    allowed_set = set(allowed_tools) if allowed_tools else set()
    orch.prompt_builder.allowed_tool_names = allowed_set
    orch.prompt_builder.scheduled_task_mode = True
    scheduled_started_at = datetime.now(tz=UTC)
    orch.scheduled_task_run = {
        "task_id": task_id,
        "started_at": scheduled_started_at.isoformat(),
    }
    orch.tool_executor.allowed_tool_names = allowed_set
    orch.tool_executor.allowed_tool_revisions = dict(allowed_authorizations)
    # Suggestions are off for a task chat by default (an autonomous run has no
    # one to click them), but the per-chat toggle can turn them back on.
    orch.prompt_builder.suggestions_enabled = await read_suggestions_enabled(
        services.db_client, conversation_id, default=False
    )

    # In structured mode the task is one authoritative turn. Tool enforcement
    # and bounded retries happen inside that state machine; the scheduler never
    # starts a second chat attempt or treats a requested tool as successful.
    required_tools = set(allowed_set)
    response = None
    turn_id = ""
    missing_tools: set[str] = set()
    cumulative_used: set[str] = set()
    try:
        turn_id = str(uuid.uuid4())
        await services.db_client.create_agent_turn(
            conversation_id=conversation_id,
            turn_id=turn_id,
            session_id=f"sched_{task_id}",
            channel="scheduler",
        )
        orch.run_event_recorder = RunEventRecorder(
            db_client=services.db_client,
            context=RunEventContext(
                run_id=turn_id,
                request_id=f"sched_{task_id}",
                conversation_id=conversation_id,
                architecture_version="structured_v1",
            ),
        )
        response = await orch.process_message(
            message=prompt,
            channel="scheduler",
            llm_call=services.llm_call,
            session_id=f"sched_{task_id}",
            conversation_id=conversation_id,
            agent_run_id=turn_id,
            forced_tools=sorted(required_tools),
        )
        services.atomic_task_result_committed = bool(
            getattr(response, "outcome", None) is not None
        )
        canonical = await services.db_client.get_agent_turn(turn_id)
        obligations = {
            str(item.get("obligation_id", "")): str(item.get("required_tool", ""))
            for item in (canonical or {}).get("obligations", [])
            if isinstance(item, dict)
        }
        bound_ids = {
            str(item.get("obligation_id", ""))
            for item in (canonical or {}).get("binds", [])
            if isinstance(item, dict)
        }
        cumulative_used = {
            tool_name
            for obligation_id, tool_name in obligations.items()
            if obligation_id in bound_ids
        }
        result_text = (
            response.structured_content.answer
            if response is not None and response.structured_content
            else ""
        ) or ""
        typed_failure = bool(
            response is not None
            and getattr(response, "outcome", None) is not None
            and response.outcome.kind.value != "final"
        )
        if typed_failure or (required_tools and not cumulative_used >= required_tools):
            missing_tools = required_tools - cumulative_used
            if missing_tools:
                result_text = (
                    "Scheduled task failed because required tools were not applied: "
                    f"{', '.join(sorted(missing_tools))}."
                )
            elif not result_text:
                result_text = "Scheduled task failed before producing a final result."
            success = False
        else:
            success = True
    except asyncio.CancelledError:
        finished_at = datetime.now(tz=UTC)
        if turn_id:
            await services.db_client.cancel_agent_turn(
                turn_id,
                reason="Scheduled task execution timed out.",
                timeout=True,
                task_result={
                    "task_id": task_id,
                    "started_at": scheduled_started_at.isoformat(),
                    "finished_at": finished_at.isoformat(),
                    "status": "timeout",
                    "result_text": "",
                    "error_message": "Scheduled task execution timed out.",
                    "duration_ms": max(
                        0,
                        int(
                            (finished_at - scheduled_started_at).total_seconds() * 1000
                        ),
                    ),
                },
            )
            services.atomic_task_result_committed = True
        raise
    except Exception as exc:
        logger.exception("Agent task %s (%s) failed", task_id, task_name)
        canonical = (
            await services.db_client.get_agent_turn(turn_id) if turn_id else None
        )
        terminal_outcome = str((canonical or {}).get("terminal_outcome") or "")
        if terminal_outcome in {"final", "failed"}:
            services.atomic_task_result_committed = True
            payload = (canonical or {}).get("outcome_payload")
            outcome_payload = payload if isinstance(payload, dict) else {}
            structured = outcome_payload.get("structured_content")
            structured_payload = structured if isinstance(structured, dict) else {}
            error = outcome_payload.get("error")
            error_payload = error if isinstance(error, dict) else {}
            result_text = str(
                structured_payload.get("answer")
                or error_payload.get("message")
                or "Scheduled task failed after committing its outcome."
            )
            success = terminal_outcome == "final"
        else:
            result_text = str(exc)
            success = False
            if turn_id:
                finished_at = datetime.now(tz=UTC)
                await services.db_client.cancel_agent_turn(
                    turn_id,
                    reason=result_text[:1000] or "Scheduled task execution failed.",
                    failure=True,
                    task_result={
                        "task_id": task_id,
                        "started_at": scheduled_started_at.isoformat(),
                        "finished_at": finished_at.isoformat(),
                        "status": "error",
                        "result_text": "",
                        "error_message": result_text[:1000],
                        "duration_ms": max(
                            0,
                            int(
                                (finished_at - scheduled_started_at).total_seconds()
                                * 1000
                            ),
                        ),
                    },
                )
                services.atomic_task_result_committed = True
        missing_tools = set()
    finally:
        orch.prompt_builder.allowed_tool_names = None
        orch.prompt_builder.scheduled_task_mode = False
        orch.prompt_builder.suggestions_enabled = True
        orch.tool_executor.allowed_tool_names = None
        orch.tool_executor.allowed_tool_revisions = {}
        orch.run_event_recorder = None
        orch.scheduled_task_run = None

    # Detect [SILENT] marker — agent decided nothing is worth reporting
    is_silent = success and result_text.strip().startswith("[SILENT]")
    if is_silent:
        logger.info(
            "Task %s (%s) reported [SILENT], skipping delivery",
            task_id,
            task_name,
        )
        return "[SILENT]", True

    await _emit_result_event(services, task, result_text, success=success)
    if success:
        # Deliver through the bridge of the conversation that actually holds
        # the result — the task's own sched_ chat — under the real persisted
        # message id (so a bridge reconnect's history backfill dedups it and
        # does not re-post). The origin conversation is NOT the target: the
        # scheduler writes its messages to sched_<task_id>, so that is where a
        # user attaches a bridge and expects results to flow out.
        assistant_message_id = (
            response.assistant_message_id if response is not None else None
        )
        await _deliver_to_bridge(
            services,
            task,
            result_text,
            conversation_id=conversation_id,
            message_id=assistant_message_id,
        )
    return result_text, success


def _parse_allowed_tool_authorizations(
    raw: str,
    broker: ToolBroker,
) -> dict[str, str]:
    """Validate persisted scheduler grants against the live policy revision."""
    try:
        entries = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("malformed allowlist") from exc
    if not isinstance(entries, list):
        raise ValueError("allowlist must be an array")
    authorizations: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("legacy name-only allowlist is quarantined")
        name = entry.get("tool_name")
        revision = entry.get("approved_policy_revision")
        if not isinstance(name, str) or not isinstance(revision, str):
            raise ValueError("authorization requires name and policy revision")
        definition = broker.registry.get(name)
        if definition is None:
            raise ValueError(f"unknown tool {name}")
        if definition.permission.revision != revision:
            raise ValueError(f"stale policy revision for {name}")
        if name in authorizations:
            raise ValueError(f"duplicate tool {name}")
        authorizations[name] = revision
    return authorizations


async def _emit_result_event(
    services: SchedulerServices,
    task: dict[str, Any],
    result_text: str,
    *,
    success: bool,
) -> None:
    """Emit an SSE event with the task result."""
    if services.sse_publish_fn is None:
        return

    from backend.ipc.models import StreamEvent

    event = StreamEvent(
        request_id=f"sched_{task['id']}",
        event_type=StreamEventType.SCHEDULED_TASK_RESULT,
        data={
            "task_id": task["id"],
            "task_name": task["name"],
            "task_type": task["task_type"],
            "conversation_id": f"sched_{task['id']}",
            "result_text": result_text[:2000],
            "status": "ok" if success else "error",
        },
    )

    try:
        await services.sse_publish_fn(event)
    except Exception:
        logger.exception("Failed to emit SSE event for task %s", task["id"])


async def _deliver_to_bridge(
    services: SchedulerServices,
    task: dict[str, Any],
    result_text: str,
    *,
    conversation_id: str | None = None,
    message_id: str | None = None,
) -> None:
    """Deliver a task result outward through a conversation's chat bridge.

    The bridge only forwards if the target conversation has an active
    outbound bridge link; otherwise this is a no-op.

    ``conversation_id`` selects which conversation's bridge to use. Agent tasks
    pass their own sched_<task_id> chat (where the result is persisted) so the
    bridge attached to that chat forwards it. When omitted (reminders), the
    task's recorded origin conversation is used.

    ``message_id`` should be the real persisted assistant message id so the
    bridge's reconnect backfill dedups it; when omitted a synthetic id is used.
    """
    if services.bridge_service is None:
        return

    target_conversation_id = conversation_id or task.get("conversation_id") or ""
    if not target_conversation_id:
        return

    out_message_id = message_id or (
        f"sched_msg_{task['id']}_{int(datetime.now(tz=UTC).timestamp())}"
    )
    # An agent task's result is already a complete, self-contained answer, so
    # send it bare (no label). A reminder carries only its reminder text, so
    # keep a short label to mark what it is.
    if task["task_type"] == "reminder":
        formatted = f"[Reminder] {task['name']}\n\n{result_text}"
    else:
        formatted = result_text

    try:
        await services.bridge_service.sync_outbound_message(
            conversation_id=target_conversation_id,
            message_id=out_message_id,
            role="assistant",
            content=formatted,
        )
    except Exception:
        logger.exception(
            "Failed to deliver task %s to bridge for conversation %s",
            task["id"],
            target_conversation_id,
        )
