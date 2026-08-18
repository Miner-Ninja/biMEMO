"""Tests for ToolExecutor action-decision integration."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from backend.agent.agent_loop_models import ToolCall
from backend.agent.tool_executor import ToolExecutor
from backend.permissions import ActionDecision, DecisionKind
from backend.tools.broker import ToolResult
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
        return ActionDecision(
            DecisionKind.ASK_ONCE,
            details={
                "policy_revision": "registry-v1",
                "scheduler_preauthorizable": context.tool_name
                not in {"execute_command", "execute_command_stream"},
            },
        )


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
        broker.execute_prepared = AsyncMock()
        broker.execute_prepared.return_value = ToolResult(
            tool_name="file_read",
            success=True,
            result="ok",
            duration_ms=1.0,
        )
        executor = ToolExecutor(
            broker,
            ConfirmationManager(),
            action_decisions=_AllowingDecisions(),
        )

        result = await executor._execute_with_status(ToolCall("1", "file_read", {}))

        assert result.result == "ok"
        broker.execute_prepared.assert_awaited_once()
        assert executor.executed_tools == [
            {
                "id": "1",
                "name": "file_read",
                "parameters": {},
                "permission_mode": "implicit",
                "authorization_source": "unspecified",
                "duration_ms": 1.0,
                "status": "succeeded",
                "success": True,
            }
        ]

    def test_identical_model_calls_are_collapsed_before_execution(self):
        """One duplicated HTTP request produces one canonical invocation."""
        executor = ToolExecutor(
            MagicMock(),
            ConfirmationManager(),
            action_decisions=_AllowingDecisions(),
        )
        url = "https://api.example.test/price"
        response = SimpleNamespace(
            tool_calls=[
                SimpleNamespace(
                    id=f"call-{index}",
                    name="http_fetch",
                    arguments={"url": url},
                )
                for index in range(3)
            ],
            raw_message={
                "role": "assistant",
                "tool_calls": [
                    {"id": f"call-{index}", "type": "function"} for index in range(3)
                ],
            },
        )

        calls = executor.build_tool_calls(response)
        assistant_message = executor._assistant_message_for_calls(response, calls)

        assert [call.id for call in calls] == ["call-0"]
        assert assistant_message["tool_calls"] == [{"id": "call-0", "type": "function"}]

    async def test_failed_result_is_not_successful_evidence(self):
        """A broker failure result remains failed in the execution ledger."""
        broker = MagicMock()
        broker.execute_prepared = AsyncMock(
            return_value=ToolResult(
                tool_name="file_read",
                success=False,
                result="read failed",
                duration_ms=1.0,
            )
        )
        executor = ToolExecutor(
            broker,
            ConfirmationManager(),
            action_decisions=_AllowingDecisions(),
        )

        await executor._execute_with_status(ToolCall("1", "file_read", {}))

        assert executor.executed_tools[0]["status"] == "failed"
        assert executor.executed_tools[0]["success"] is False

    async def test_truncated_mandatory_result_cannot_satisfy_evidence(self):
        """A bounded excerpt is audit data, not complete mandatory evidence."""
        broker = MagicMock()
        broker.execute_prepared = AsyncMock(
            return_value=ToolResult(
                tool_name="file_read",
                success=True,
                result="x" * 70_000,
                duration_ms=1.0,
            )
        )
        turn_store = SimpleNamespace(
            claim_agent_invocation=AsyncMock(
                return_value={"claimed": True, "claim_generation": 1}
            ),
            complete_agent_invocation=AsyncMock(),
        )
        executor = ToolExecutor(
            broker,
            ConfirmationManager(),
            action_decisions=_AllowingDecisions(),
            turn_store=turn_store,
        )
        executor.current_context = {
            "agent_run_id": "run-1",
        }
        executor.current_obligations = (SimpleNamespace(required_tool="file_read"),)

        await executor._execute_with_status(ToolCall("call-1", "file_read", {}))

        assert executor.executed_tools[0]["status"] == "failed"
        completion = turn_store.complete_agent_invocation.await_args.kwargs
        assert completion["status"] == "failed"
        assert "byte limit" in completion["error"]

    def test_core_tool_message_uses_backend_identity_and_omits_draft(self):
        """Stage 1 prose is dropped and provenance uses the backend invocation ID."""
        executor = ToolExecutor(MagicMock(), ConfirmationManager())
        executor.current_context = {
            "agent_run_id": "run-1",
        }
        messages = []
        call = ToolCall("provider-call-1", "file_read", {})
        executor.append_tool_results_to_messages(
            messages=messages,
            llm_response=SimpleNamespace(
                raw_message={
                    "role": "assistant",
                    "content": "uncommitted draft",
                    "tool_calls": [{"id": call.id}],
                }
            ),
            immediate_calls=[call],
            results=[
                ToolResult(
                    tool_name="file_read",
                    success=True,
                    result="evidence",
                    duration_ms=1.0,
                )
            ],
        )

        assert messages[0]["content"] == ""
        envelope = json.loads(messages[1]["content"])
        assert envelope["invocation_id"] == executor._invocation_id(call.id)
        assert envelope["invocation_id"] != call.id

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
        executor.allowed_tool_revisions = {"http_fetch": "registry-v1"}

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

    def test_scheduler_command_tools_never_receive_preauthorization(self):
        """General host commands remain interactive until OS sandboxing exists."""
        executor = self._executor(_ConfirmDecisions())
        executor.current_context = {"channel": "scheduler"}
        executor.allowed_tool_names = {"execute_command"}
        executor.allowed_tool_revisions = {"execute_command": "registry-v1"}

        immediate, still_red = executor._promote_saved_grants(
            [ToolCall("1", "execute_command", {"command": "python -c pass"})]
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


class TestBatchFailureContainment:
    """One failing tool must not abort its siblings or the round."""

    def _executor_with_stubbed_execution(self, outcomes: dict) -> ToolExecutor:
        """Build an executor whose per-call execution is replaced by a map."""
        executor = ToolExecutor(
            MagicMock(),
            ConfirmationManager(),
            action_decisions=_AllowingDecisions(),
        )

        async def fake_execute(tool_call):
            outcome = outcomes[tool_call.name]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        executor._execute_with_status = fake_execute
        return executor

    async def test_sibling_failure_becomes_failed_result(self):
        """A raising handler yields a failed ToolResult, siblings survive."""
        ok = ToolResult(
            tool_name="file_read",
            success=True,
            result="content",
            duration_ms=1.0,
        )
        executor = self._executor_with_stubbed_execution(
            {
                "file_read": ok,
                "web_search": RuntimeError("searx is down"),
            }
        )

        results = await executor.execute_immediate_tool_calls(
            [
                ToolCall("1", "file_read", {"path": "/tmp/x"}),
                ToolCall("2", "web_search", {"query": "y"}),
            ]
        )

        assert len(results) == 2
        assert results[0] is ok
        assert results[1].success is False
        assert "searx is down" in str(results[1].result)
        assert results[1].authorization.source == "execution_error"

    async def test_all_failures_still_return_per_call_results(self):
        """Every call gets a result even when every handler raises."""
        executor = self._executor_with_stubbed_execution(
            {
                "file_read": RuntimeError("boom-a"),
                "web_search": RuntimeError("boom-b"),
            }
        )

        results = await executor.execute_immediate_tool_calls(
            [
                ToolCall("1", "file_read", {}),
                ToolCall("2", "web_search", {}),
            ]
        )

        assert [r.success for r in results] == [False, False]
        assert "boom-a" in str(results[0].result)
        assert "boom-b" in str(results[1].result)
