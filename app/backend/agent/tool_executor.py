"""Tool execution delegate for the gateway orchestrator.

Handles building tool calls from LLM responses, executing them
through the broker with trust-zone enforcement, emitting SSE
notifications for yellow-zone results, and queuing red-zone
confirmations. Manages the pending red-call state for the
orchestrator's public API.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from backend.agent.agent_loop_helpers import (
    build_tool_result_messages,
    split_by_zone,
)
from backend.agent.agent_loop_models import (
    ToolCall,
    ToolRoundResult,
)
from backend.agent.stage_timings import StageTimings, humanize_stage_label
from backend.permissions import (
    ActionContext,
    ActionDecision,
    ActionDecisionService,
    DecisionKind,
)
from backend.trust.zones import TrustZone, get_zone


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from backend.tools.broker import ToolBroker, ToolResult
    from backend.trust.confirmation import ConfirmationManager, PendingAction

    PendingActionRecorder = Callable[..., Awaitable[dict[str, object] | None]]


logger = logging.getLogger(__name__)


class ToolExecutor:
    """Execute tool calls with trust zone enforcement.

    Attributes:
        broker: Tool broker for execution.
        confirmation_mgr: Manager for red-zone confirmations.
    """

    def __init__(
        self,
        broker: ToolBroker,
        confirmation_mgr: ConfirmationManager,
        action_decisions: ActionDecisionService | None = None,
        pending_action_recorder: PendingActionRecorder | None = None,
    ) -> None:
        """Initialize tool executor.

        Args:
            broker: Tool broker for execution.
            confirmation_mgr: Manager for red-zone confirmations.
            action_decisions: Optional action decision service.
            pending_action_recorder: Optional async persistence hook for
                confirmation-required actions.
        """
        self.broker = broker
        self.confirmation_mgr = confirmation_mgr
        self.action_decisions = action_decisions or ActionDecisionService()
        self.pending_action_recorder = pending_action_recorder
        self.sse_queue: object | None = None
        self._pending_red: list[PendingAction] = []
        self.tool_call_count: int = 0
        self.tool_names_used: list[str] = []
        self.allowed_tool_names: set[str] | None = None
        # Context propagated to tool handlers via the broker
        # (e.g. {"conversation_id": ..., "session_id": ...}).
        # Set by the orchestrator before processing a turn.
        self.current_context: dict[str, str] = {}
        # Per-turn processing-stage accumulator, assigned by the orchestrator
        # before each turn so tool durations join recall/context timings.
        self.stage_timings: StageTimings | None = None

    def build_tool_calls(self, llm_response: object) -> list[ToolCall]:
        """Convert raw LLM tool calls into the internal tool-call shape."""
        return [
            ToolCall(
                id=tool_call.id,
                name=tool_call.name,
                parameters=tool_call.arguments,
            )
            for tool_call in llm_response.tool_calls
        ]

    async def _execute_with_status(self, tool_call: ToolCall) -> ToolResult:
        """Execute one tool call and emit SSE status events around it."""
        self.tool_call_count += 1
        self.tool_names_used.append(tool_call.name)
        # Allowlist enforcement (used by scheduler for cron tasks)
        if (
            self.allowed_tool_names is not None
            and tool_call.name not in self.allowed_tool_names
        ):
            msg = (
                f"Tool '{tool_call.name}' is not in the allowlist "
                "for this scheduled task"
            )
            raise PermissionError(msg)
        decision = self._decide_tool_action(tool_call)
        if decision.kind == DecisionKind.BLOCK:
            msg = f"Tool action blocked: {decision.reason}"
            if self.sse_queue is not None:
                await self.sse_queue.put(
                    {"type": "tool_status", "name": tool_call.name, "status": "error"}
                )
            raise PermissionError(msg)
        if self.sse_queue is not None:
            await self.sse_queue.put(
                {"type": "tool_status", "name": tool_call.name, "status": "started"}
            )
        # Push the current execution context into the broker so handlers
        # that need it (e.g. schedule_task) can read conversation_id from
        # broker.current_context. Cleared in finally to keep state clean.
        self.broker.set_context(self.current_context)
        try:
            result = await self.broker.execute(tool_call.name, tool_call.parameters)
        except Exception:
            if self.sse_queue is not None:
                await self.sse_queue.put(
                    {"type": "tool_status", "name": tool_call.name, "status": "error"}
                )
            raise
        finally:
            self.broker.clear_context()
        if self.stage_timings is not None:
            self.stage_timings.record(
                humanize_stage_label(tool_call.name), result.duration_ms
            )
        if self.sse_queue is not None:
            await self.sse_queue.put(
                {"type": "tool_status", "name": tool_call.name, "status": "completed"}
            )
        return result

    def _action_context(self, tool_call: ToolCall) -> ActionContext:
        """Build the action policy context for one tool call."""
        return ActionContext(
            conversation_id=self.current_context.get("conversation_id", ""),
            channel=self.current_context.get("channel", ""),
            session_id=self.current_context.get("session_id", ""),
            tool_name=tool_call.name,
            parameters=tool_call.parameters,
            trust_zone=get_zone(tool_call.name).value,
        )

    def _decide_tool_action(self, tool_call: ToolCall) -> ActionDecision:
        """Evaluate one tool call against the action policy."""
        return self.action_decisions.decide(self._action_context(tool_call))

    def _scheduled_task_preauthorized(self, call: ToolCall) -> bool:
        """Whether an autonomous scheduled run pre-authorises this RED tool.

        A scheduled task's own allowlist is the user's explicit authorization, so
        a confirmation-required tool in it runs without an interactive
        confirmation that can never happen in an autonomous run. Bounded to the
        scheduler channel and the task allowlist only.
        """
        return (
            self.current_context.get("channel") == "scheduler"
            and self.allowed_tool_names is not None
            and call.name in self.allowed_tool_names
        )

    def _promote_saved_grants(
        self,
        red_calls: list[ToolCall],
    ) -> tuple[list[ToolCall], list[ToolCall]]:
        """Move already granted RED calls into the immediate bucket."""
        immediate: list[ToolCall] = []
        still_red: list[ToolCall] = []
        for call in red_calls:
            decision = self._decide_tool_action(call)
            if decision.kind == DecisionKind.ALLOW:
                immediate.append(call)
            elif decision.kind == DecisionKind.BLOCK:
                raise PermissionError(f"Tool action blocked: {decision.reason}")
            elif self._scheduled_task_preauthorized(call):
                immediate.append(call)
            else:
                still_red.append(call)
        return immediate, still_red

    async def execute_immediate_tool_calls(
        self,
        immediate_calls: list[ToolCall],
    ) -> list[ToolResult]:
        """Run all non-confirmation tool calls for one model round."""
        return list(
            await asyncio.gather(
                *[self._execute_with_status(tool_call) for tool_call in immediate_calls]
            )
        )

    async def emit_yellow_tool_notifications(
        self,
        results: list[ToolResult],
    ) -> None:
        """Send short SSE notifications for yellow-zone tool results."""
        if self.sse_queue is None:
            return
        for result in results:
            if result.zone == TrustZone.YELLOW:
                await self.sse_queue.put(
                    {
                        "type": "notification",
                        "tool": result.tool_name,
                        "summary": str(result.result)[:200],
                    }
                )

    async def queue_red_tool_confirmations(
        self,
        red_calls: list[ToolCall],
        channel: str,
        messages: list[dict[str, object]] | None = None,
        assistant_message: dict[str, object] | None = None,
    ) -> None:
        """Queue confirmation requests for red-zone tool calls."""
        pending_actions: list[PendingAction] = []
        for call in red_calls:
            action = self.confirmation_mgr.request_confirmation(
                call.name,
                call.parameters,
                channel,
            )
            await self._persist_pending_action(
                call,
                action.action_id,
                channel,
                messages=messages,
                assistant_message=assistant_message,
            )
            await self._emit_waiting_confirmation(call, action.action_id)
            pending_actions.append(action)
        self._pending_red = pending_actions

    async def _emit_waiting_confirmation(
        self,
        call: ToolCall,
        action_id: str,
    ) -> None:
        """Emit a stream event for one action awaiting confirmation."""
        if self.sse_queue is None:
            return
        await self.sse_queue.put(
            {
                "type": "confirmation",
                "status": "waiting_confirmation",
                "action_id": action_id,
                "tool_name": call.name,
                "parameters": call.parameters,
            }
        )

    async def _persist_pending_action(
        self,
        call: ToolCall,
        action_id: str,
        channel: str,
        messages: list[dict[str, object]] | None = None,
        assistant_message: dict[str, object] | None = None,
    ) -> None:
        """Persist one confirmation-required action when a recorder is configured."""
        if self.pending_action_recorder is None:
            return
        run_id = self.current_context.get("agent_run_id", "")
        if not run_id:
            return
        await self.pending_action_recorder(
            run_id=run_id,
            action_id=action_id,
            tool_name=call.name,
            parameters=call.parameters,
            channel=channel,
            preview={
                "action_id": action_id,
                "tool_call_id": call.id,
            },
            resume_snapshot={
                "tool_call_id": call.id,
                "messages": messages or [],
                "assistant_message": assistant_message or {},
            },
        )

    def append_tool_results_to_messages(
        self,
        *,
        messages: list[dict[str, object]],
        llm_response: object,
        immediate_calls: list[ToolCall],
        results: list[ToolResult],
    ) -> None:
        """Append the raw assistant tool-call turn and tool results."""
        if not results:
            return
        messages.append(llm_response.raw_message)
        messages.extend(build_tool_result_messages(immediate_calls, results))

    async def run_one_tool_round(
        self,
        *,
        llm_response: object,
        messages: list[dict[str, object]],
        channel: str,
        user_intent: dict[str, set[str]] | None = None,
    ) -> ToolRoundResult:
        """Execute one batch of tool calls and update the prompt state."""
        if not llm_response.tool_calls:
            return ToolRoundResult(
                raw_final_text=llm_response.text,
                structured_content=llm_response.structured_content,
                executed_immediate_tools=False,
                should_continue=False,
            )

        tool_calls = self.build_tool_calls(llm_response)
        immediate, red = split_by_zone(
            tool_calls,
            user_intent=user_intent,
        )
        granted_red, red = self._promote_saved_grants(red)
        immediate.extend(granted_red)
        results = await self.execute_immediate_tool_calls(immediate)
        await self.emit_yellow_tool_notifications(results)
        await self.queue_red_tool_confirmations(
            red,
            channel,
            messages=messages,
            assistant_message=llm_response.raw_message,
        )
        self.append_tool_results_to_messages(
            messages=messages,
            llm_response=llm_response,
            immediate_calls=immediate,
            results=results,
        )

        if not immediate and red:
            return ToolRoundResult(
                raw_final_text=llm_response.text,
                structured_content=llm_response.structured_content,
                executed_immediate_tools=False,
                should_continue=False,
            )

        return ToolRoundResult(
            raw_final_text="",
            structured_content=None,
            executed_immediate_tools=bool(results),
            should_continue=True,
        )

    @property
    def pending_red_calls(self) -> list[PendingAction]:
        """Return pending RED zone actions."""
        return list(self._pending_red)

    def clear_pending(self) -> None:
        """Clear pending RED zone calls."""
        self._pending_red.clear()
