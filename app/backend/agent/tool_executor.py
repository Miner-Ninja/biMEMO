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
from backend.trust.zones import TrustZone


if TYPE_CHECKING:
    from backend.tools.broker import ToolBroker, ToolResult
    from backend.trust.confirmation import ConfirmationManager, PendingAction


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
    ) -> None:
        """Initialize tool executor.

        Args:
            broker: Tool broker for execution.
            confirmation_mgr: Manager for red-zone confirmations.
        """
        self.broker = broker
        self.confirmation_mgr = confirmation_mgr
        self.sse_queue: object | None = None
        self._pending_red: list[PendingAction] = []
        self.tool_call_count: int = 0
        self.tool_names_used: list[str] = []
        self.allowed_tool_names: set[str] | None = None
        # Context propagated to tool handlers via the broker
        # (e.g. {"conversation_id": ..., "session_id": ...}).
        # Set by the orchestrator before processing a turn.
        self.current_context: dict[str, str] = {}

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
        if self.sse_queue is not None:
            await self.sse_queue.put(
                {"type": "tool_status", "name": tool_call.name, "status": "completed"}
            )
        return result

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

    def queue_red_tool_confirmations(
        self,
        red_calls: list[ToolCall],
        channel: str,
    ) -> None:
        """Queue confirmation requests for red-zone tool calls."""
        pending_actions: list[PendingAction] = []
        for call in red_calls:
            action = self.confirmation_mgr.request_confirmation(
                call.name,
                call.parameters,
                channel,
            )
            pending_actions.append(action)
        self._pending_red = pending_actions

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
        user_intent_tools: set[str] | None = None,
    ) -> ToolRoundResult:
        """Execute one batch of tool calls and update the prompt state."""
        if not llm_response.tool_calls:
            return ToolRoundResult(
                final_text=llm_response.text,
                executed_immediate_tools=False,
                should_continue=False,
            )

        tool_calls = self.build_tool_calls(llm_response)
        immediate, red = split_by_zone(
            tool_calls,
            user_intent_tools=user_intent_tools,
        )
        results = await self.execute_immediate_tool_calls(immediate)
        await self.emit_yellow_tool_notifications(results)
        self.queue_red_tool_confirmations(red, channel)
        self.append_tool_results_to_messages(
            messages=messages,
            llm_response=llm_response,
            immediate_calls=immediate,
            results=results,
        )

        if not immediate and red:
            return ToolRoundResult(
                final_text=llm_response.text,
                executed_immediate_tools=False,
                should_continue=False,
            )

        return ToolRoundResult(
            final_text="",
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
