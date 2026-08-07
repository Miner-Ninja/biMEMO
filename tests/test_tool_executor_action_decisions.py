"""Tests for ToolExecutor action-decision integration."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from backend.agent.agent_loop_models import ToolCall
from backend.agent.tool_executor import ToolExecutor
from backend.permissions import ActionDecision, DecisionKind
from backend.trust.confirmation import ConfirmationManager


class _BlockingDecisions:
    """Fake action decision service that blocks all calls."""

    def decide(self, context):
        """Return a blocking decision."""
        return ActionDecision(DecisionKind.BLOCK, "blocked in test")


class _AllowingDecisions:
    """Fake action decision service that allows all calls."""

    def decide(self, context):
        """Return an allowing decision."""
        return ActionDecision(DecisionKind.ALLOW)


class _ConfirmDecisions:
    """Fake decision service that requires confirmation for all calls."""

    def decide(self, context):
        """Return a confirmation-required decision (not ALLOW, not BLOCK)."""
        return ActionDecision(DecisionKind.ASK_ONCE)


class _PendingActionRecorder:
    """Fake pending-action recorder."""

    def __init__(self):
        """Initialize recorded calls."""
        self.calls = []

    async def create(self, **kwargs):
        """Record one pending-action create call."""
        self.calls.append(kwargs)
        return {"id": kwargs.get("action_id")}


class TestToolExecutorActionDecisions:
    """Verify action decisions gate broker execution."""

    async def test_blocked_decision_does_not_call_broker(self):
        """Skip broker execution when a decision blocks the call."""
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
        """Call the broker when a decision allows the call."""
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
        """Promote a red call to immediate execution when grants allow it."""
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
        """Do not queue a blocked red call for confirmation."""
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
        """Persist a red confirmation when an agent run context exists."""
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
        """Emit a waiting-confirmation event for red calls."""
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


class TestScheduledTaskPreauthorization:
    """A scheduled task's allowlist pre-authorizes its RED tools.

    In an autonomous scheduled run there is no live user to answer a RED
    confirmation, so a confirmation-required tool that the user explicitly
    put in the task allowlist is promoted to immediate execution — bounded
    strictly to the scheduler channel and that allowlist, never touching
    interactive sessions or tools outside the list, and never overriding a
    security BLOCK.
    """

    def _executor(self, decisions):
        """Build a ToolExecutor with the given fake decision service."""
        return ToolExecutor(
            MagicMock(),
            ConfirmationManager(),
            action_decisions=decisions,
        )

    def test_scheduler_allowlisted_red_promoted(self):
        """channel=scheduler + tool in allowlist -> immediate (R1)."""
        executor = self._executor(_ConfirmDecisions())
        executor.current_context = {"channel": "scheduler"}
        executor.allowed_tool_names = {"http_fetch"}

        immediate, still_red = executor._promote_saved_grants(
            [ToolCall("1", "http_fetch", {"url": "https://example.com"})]
        )

        assert len(immediate) == 1
        assert still_red == []

    def test_interactive_red_not_promoted(self):
        """Non-scheduler channel -> still queued for confirmation (R2)."""
        executor = self._executor(_ConfirmDecisions())
        executor.current_context = {"channel": "web"}
        executor.allowed_tool_names = {"http_fetch"}

        immediate, still_red = executor._promote_saved_grants(
            [ToolCall("1", "http_fetch", {"url": "https://example.com"})]
        )

        assert immediate == []
        assert len(still_red) == 1

    def test_scheduler_non_allowlisted_red_not_promoted(self):
        """channel=scheduler but tool not in allowlist -> not promoted (R4)."""
        executor = self._executor(_ConfirmDecisions())
        executor.current_context = {"channel": "scheduler"}
        executor.allowed_tool_names = {"web_search"}

        immediate, still_red = executor._promote_saved_grants(
            [ToolCall("1", "http_fetch", {"url": "https://example.com"})]
        )

        assert immediate == []
        assert len(still_red) == 1

    def test_blocked_call_still_raises_on_scheduler(self):
        """A security BLOCK raises even on the scheduler channel (R3)."""
        executor = self._executor(_BlockingDecisions())
        executor.current_context = {"channel": "scheduler"}
        executor.allowed_tool_names = {"http_fetch"}

        with pytest.raises(PermissionError, match="blocked in test"):
            executor._promote_saved_grants(
                [ToolCall("1", "http_fetch", {"url": "https://example.com"})]
            )
