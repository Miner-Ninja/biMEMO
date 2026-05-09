"""Tests for ToolExecutor action-decision integration."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.agent.agent_loop_models import ToolCall
from backend.agent.tool_executor import ToolExecutor
from backend.permissions import ActionDecision, DecisionKind
from backend.trust.confirmation import ConfirmationManager


class _BlockingDecisions:
    def decide(self, context):
        return ActionDecision(DecisionKind.BLOCK, "blocked in test")


class _AllowingDecisions:
    def decide(self, context):
        return ActionDecision(DecisionKind.ALLOW)


class _PendingActionRecorder:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": kwargs.get("action_id")}


class TestToolExecutorActionDecisions:
    """Verify action decisions gate broker execution."""

    async def test_blocked_decision_does_not_call_broker(self):
        broker = MagicMock()
        broker.execute = AsyncMock()
        executor = ToolExecutor(
            broker,
            ConfirmationManager(),
            action_decisions=_BlockingDecisions(),
        )

        with pytest.raises(PermissionError, match="blocked in test"):
            await executor._execute_with_status(ToolCall("1", "file_read", {}))

        broker.execute.assert_not_awaited()

    async def test_allowed_decision_calls_broker(self):
        broker = MagicMock()
        broker.execute = AsyncMock()
        broker.execute.return_value = "ok"
        executor = ToolExecutor(
            broker,
            ConfirmationManager(),
            action_decisions=_AllowingDecisions(),
        )

        result = await executor._execute_with_status(ToolCall("1", "file_read", {}))

        assert result == "ok"
        broker.execute.assert_awaited_once_with("file_read", {})

    def test_saved_grant_promotes_red_call_to_immediate(self):
        broker = MagicMock()
        executor = ToolExecutor(
            broker,
            ConfirmationManager(),
            action_decisions=_AllowingDecisions(),
        )

        immediate, still_red = executor._promote_saved_grants(
            [ToolCall("1", "http_fetch", {"url": "https://example.com"})]
        )

        assert len(immediate) == 1
        assert still_red == []

    def test_blocked_red_call_is_not_queued_for_confirmation(self):
        broker = MagicMock()
        executor = ToolExecutor(
            broker,
            ConfirmationManager(),
            action_decisions=_BlockingDecisions(),
        )

        with pytest.raises(PermissionError, match="blocked in test"):
            executor._promote_saved_grants(
                [ToolCall("1", "http_fetch", {"url": "https://example.com"})]
            )

    async def test_red_confirmation_is_persisted_when_run_context_exists(self):
        broker = MagicMock()
        recorder = _PendingActionRecorder()
        executor = ToolExecutor(
            broker,
            ConfirmationManager(),
            pending_action_recorder=recorder.create,
        )
        executor.current_context = {"agent_run_id": "run-1"}

        await executor.queue_red_tool_confirmations(
            [ToolCall("1", "email_send", {"to": "a@example.com"})],
            "web",
            messages=[{"role": "user", "content": "send it"}],
            assistant_message={"role": "assistant", "tool_calls": []},
        )

        assert len(recorder.calls) == 1
        assert recorder.calls[0]["run_id"] == "run-1"
        assert recorder.calls[0]["tool_name"] == "email_send"
        assert recorder.calls[0]["action_id"] == executor.pending_red_calls[0].action_id
        assert recorder.calls[0]["preview"]["tool_call_id"] == "1"
        assert "messages" not in recorder.calls[0]["preview"]
        assert recorder.calls[0]["resume_snapshot"]["tool_call_id"] == "1"
        assert recorder.calls[0]["resume_snapshot"]["messages"][0]["role"] == "user"

    async def test_red_confirmation_emits_waiting_event(self):
        broker = MagicMock()
        queue: asyncio.Queue = asyncio.Queue()
        executor = ToolExecutor(broker, ConfirmationManager())
        executor.sse_queue = queue

        await executor.queue_red_tool_confirmations(
            [ToolCall("1", "email_send", {"to": "a@example.com"})],
            "web",
        )

        event = queue.get_nowait()
        assert event["type"] == "confirmation"
        assert event["status"] == "waiting_confirmation"
        assert event["tool_name"] == "email_send"
        assert event["action_id"] == executor.pending_red_calls[0].action_id
