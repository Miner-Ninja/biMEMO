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

from backend.ipc.models import StreamEventType


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

    # Create isolated conversation ID
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    conversation_id = f"sched_{task_id}_{timestamp}"

    # Create the conversation in DB
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

    # Run the agentic loop
    try:
        response = await orch.process_message(
            message=prompt,
            channel="scheduler",
            llm_call=services.llm_call,
            session_id=f"sched_{task_id}",
            conversation_id=conversation_id,
        )
        result_text = response.text or ""
        success = True
    except Exception as exc:
        logger.exception("Agent task %s (%s) failed", task_id, task_name)
        result_text = str(exc)
        success = False
    finally:
        orch.prompt_builder.allowed_tool_names = None
        orch.prompt_builder.scheduled_task_mode = False
        orch.tool_executor.allowed_tool_names = None

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
        await _deliver_to_bridge(services, task, result_text)
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
) -> None:
    """Deliver task result to the chat bridge of the originating conversation.

    Bridges are linked to the conversation_id stored in the task. The
    isolated scheduler conversation (sched_*) has no bridge, so we
    target the original user conversation if one is recorded.
    """
    if services.bridge_service is None:
        return

    origin_conversation_id = task.get("conversation_id") or ""
    if not origin_conversation_id:
        return

    task_name = task["name"]
    task_type = task["task_type"]
    label = "Reminder" if task_type == "reminder" else "Scheduled task"
    message_id = f"sched_msg_{task['id']}_{int(datetime.now(tz=UTC).timestamp())}"
    formatted = f"[{label}] {task_name}\n\n{result_text}"

    try:
        await services.bridge_service.sync_outbound_message(
            conversation_id=origin_conversation_id,
            message_id=message_id,
            role="assistant",
            content=formatted,
        )
    except Exception:
        logger.exception(
            "Failed to deliver task %s to bridge for conversation %s",
            task["id"],
            origin_conversation_id,
        )
