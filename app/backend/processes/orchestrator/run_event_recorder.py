"""Runtime journal helpers for chat run events."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from backend.processes.orchestrator.client import DBProxyClient


STREAM_EVENT_TYPE_KEY = "type"


class AgentRunEventType(StrEnum):
    """Closed set of persisted runtime chat run event types."""

    RUN_STARTED = "run_started"
    TOOL_STATUS = "tool_status"
    FORCED_TOOLS = "forced_tools"
    REQUIREMENTS = "requirements"
    TOOL_PLAN = "tool_plan"
    REASK_DECISION = "reask_decision"
    LLM_STARTED = "llm_started"
    ANSWER_DELTA = "answer_delta"
    THINKING_DELTA = "thinking_delta"
    FINAL_ACCEPTED = "final_accepted"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    RUN_STOPPED = "run_stopped"
    RUN_CANCELLED = "run_cancelled"


STREAM_TO_RUN_EVENT_TYPE = {
    "tool_status": AgentRunEventType.TOOL_STATUS,
    "final": AgentRunEventType.FINAL_ACCEPTED,
    "error": AgentRunEventType.RUN_FAILED,
}


@dataclass
class RunEventContext:
    """Mutable runtime identifiers shared by one chat request."""

    run_id: str = ""
    request_id: str = ""
    conversation_id: str = ""
    architecture_version: str = "structured_v1"


class RunEventRecorder:
    """Persist chat run events in runtime storage."""

    def __init__(
        self,
        *,
        db_client: DBProxyClient,
        context: RunEventContext,
    ) -> None:
        """Initialize the recorder for one chat request.

        Args:
            db_client: DB proxy client used to write runtime events.
            context: Mutable run identifiers for the active request.
        """
        self._db_client = db_client
        self._context = context

    async def record(
        self,
        event_type: AgentRunEventType,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Persist one runtime event when the run context is ready."""
        if not self._context.run_id or not self._context.conversation_id:
            return None
        return await self._db_client.append_agent_turn_event(
            turn_id=self._context.run_id,
            event_type=event_type.value,
            payload=payload or {},
        )

    async def record_provider_attempt(
        self,
        *,
        phase: str,
        attempt: int,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Persist one actual HTTP provider attempt with stable phase metadata."""
        if not self._context.run_id:
            return None
        return await self._db_client.append_agent_turn_event(
            turn_id=self._context.run_id,
            event_type="provider_attempt",
            phase=phase,
            attempt=attempt,
            payload=payload,
        )

    async def record_stream_event(
        self,
        event_data: dict[str, Any],
    ) -> None:
        """Persist one stream event projection when it matters for recovery."""
        raw_event_type = str(event_data.get(STREAM_EVENT_TYPE_KEY, ""))
        event_type = STREAM_TO_RUN_EVENT_TYPE.get(raw_event_type)
        if event_type is None:
            return
        await self.record(
            event_type,
            {
                "stream_event": event_data,
            },
        )
