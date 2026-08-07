"""Task execution engine for the scheduler.

Handles executing both reminder (notification-only) and agent_task
(full agentic loop) scheduled tasks. Manages tool allowlist
filtering, conversation isolation, and result delivery via SSE.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from backend.agent.conversation_settings import read_suggestions_enabled
from backend.ipc.models import StreamEventType


if TYPE_CHECKING:
    from collections.abc import Callable

    from backend.agent.agent_loop import AgentOrchestrator
    from backend.processes.orchestrator.client import DBProxyClient
    from backend.tools.broker import ToolBroker


logger = logging.getLogger(__name__)

# A scheduled agent task's declared tools must actually be called. If the model
# does not call them, it is reinforced and retried up to this many times.
MAX_TOOL_ENFORCE_ATTEMPTS = 3


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
    allowed_tools = _parse_allowed_tools(task.get("allowed_tools", "[]"))

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
    orch.tool_executor.allowed_tool_names = allowed_set
    # Suggestions are off for a task chat by default (an autonomous run has no
    # one to click them), but the per-chat toggle can turn them back on.
    orch.prompt_builder.suggestions_enabled = await read_suggestions_enabled(
        services.db_client, conversation_id, default=False
    )

    # Run the agentic loop, requiring the task's declared tools to actually be
    # applied. The task's allowlist IS the list of tools that must run for a
    # successful execution; if the model does not call them, reinforce and retry
    # (up to MAX_TOOL_ENFORCE_ATTEMPTS). After that, report transparently which
    # tools were never applied instead of silently rejecting.
    required_tools = set(allowed_set)
    attempt_prompt = prompt
    response = None
    missing_tools: set[str] = set()
    try:
        for _attempt in range(MAX_TOOL_ENFORCE_ATTEMPTS):
            response = await orch.process_message(
                message=attempt_prompt,
                channel="scheduler",
                llm_call=services.llm_call,
                session_id=f"sched_{task_id}",
                conversation_id=conversation_id,
            )
            used = set(orch.tool_executor.tool_names_used)
            missing_tools = required_tools - used
            if not missing_tools:
                break
            attempt_prompt = (
                f"{prompt}\n\n"
                "IMPORTANT — this is an autonomous scheduled task. You MUST "
                "actually CALL these tools to complete it: "
                f"{', '.join(sorted(missing_tools))}. Do not answer from memory "
                "or earlier context — call the tools now. If a tool cannot be "
                "used or returns nothing, state that explicitly in your answer, "
                "naming the tool and the reason."
            )
        result_text = (
            response.structured_content.answer
            if response is not None and response.structured_content
            else ""
        ) or ""
        success = True
    except Exception as exc:
        logger.exception("Agent task %s (%s) failed", task_id, task_name)
        result_text = str(exc)
        success = False
        missing_tools = set()
    finally:
        orch.prompt_builder.allowed_tool_names = None
        orch.prompt_builder.scheduled_task_mode = False
        orch.prompt_builder.suggestions_enabled = True
        orch.tool_executor.allowed_tool_names = None

    # Transparent report: declared tools that never ran across all attempts.
    if success and required_tools and missing_tools:
        logger.warning(
            "Task %s (%s): required tools not applied after %d attempts: %s",
            task_id,
            task_name,
            MAX_TOOL_ENFORCE_ATTEMPTS,
            sorted(missing_tools),
        )
        result_text = (
            f"{result_text}\n\n_Note: these required tools were not applied "
            f"after {MAX_TOOL_ENFORCE_ATTEMPTS} attempts: "
            f"{', '.join(sorted(missing_tools))}._"
        ).strip()

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


def _parse_allowed_tools(raw: str) -> list[str]:
    """Parse the allowed_tools JSON string into a list."""
    try:
        tools = json.loads(raw)
        if isinstance(tools, list):
            return [str(t) for t in tools]
    except json.JSONDecodeError, TypeError:
        pass
    return []


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
