"""Tests for one-run durable scheduled tool enforcement."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from backend.scheduler.executor import SchedulerServices, _execute_agent_task


class _FakeOrchestrator:
    """Minimal AgentOrchestrator stand-in for one structured scheduler turn."""

    def __init__(self, tools_per_attempt, answer="Done."):
        self._plan = list(tools_per_attempt)
        self._answer = answer
        self.calls = []
        self.mode_during_calls = []
        self.allowed_during_calls = []
        self.prompt_builder = SimpleNamespace(
            allowed_tool_names=None,
            scheduled_task_mode=False,
        )
        self.tool_executor = SimpleNamespace(tool_names_used=[], executed_tools=[])

    async def process_message(self, *, message, channel, **_kwargs):
        idx = len(self.calls)
        self.calls.append(message)
        self.mode_during_calls.append(self.prompt_builder.scheduled_task_mode)
        self.allowed_during_calls.append(
            set(self.prompt_builder.allowed_tool_names or ())
        )
        used = self._plan[idx] if idx < len(self._plan) else self._plan[-1]
        self.tool_executor.tool_names_used = list(used)
        self.tool_executor.executed_tools = [
            {"name": name, "status": "succeeded", "success": True} for name in used
        ]
        return SimpleNamespace(
            structured_content=SimpleNamespace(answer=self._answer),
            assistant_message_id=f"msg-{idx}",
        )


def _services(orch):
    """Build SchedulerServices around a fake orchestrator."""
    db_client = AsyncMock()
    used = set(orch._plan[0])
    db_client.get_agent_turn.return_value = {
        "obligations": [
            {"obligation_id": "required-http-fetch", "required_tool": "http_fetch"}
        ],
        "binds": (
            [{"obligation_id": "required-http-fetch"}]
            if "http_fetch" in used
            else []
        ),
    }
    definition = SimpleNamespace(
        permission=SimpleNamespace(revision="registry-v1")
    )
    broker = SimpleNamespace(
        registry=SimpleNamespace(
            get=lambda name: definition if name == "http_fetch" else None
        )
    )
    return SchedulerServices(
        db_client=db_client,
        orchestrator=orch,
        llm_call=MagicMock(),
        broker=broker,
        sse_publish_fn=None,
        bridge_service=None,
    )


def _task(
    allowed=(
        '[{"tool_name":"http_fetch",'
        '"approved_policy_revision":"registry-v1"}]'
    ),
):
    """Build an agent_task dict targeting the given allowlist."""
    return {
        "id": "t1",
        "name": "BTC price",
        "prompt": "Report the current bitcoin price.",
        "task_type": "agent_task",
        "allowed_tools": allowed,
        "conversation_id": "",
    }


class TestScheduledTaskToolEnforcement:
    """Verify the declared allowlist is enforced as must-call tools."""

    async def test_tool_used_first_attempt_no_retry_no_note(self):
        """Required tool called on attempt 1 -> one run, clean answer."""
        orch = _FakeOrchestrator([["http_fetch"]])
        result, success = await _execute_agent_task(_services(orch), _task())

        assert success is True
        assert len(orch.calls) == 1
        assert result == "Done."
        assert "not applied" not in result

    async def test_missing_bind_does_not_start_an_outer_retry(self):
        """The state machine owns retries; scheduler starts exactly one turn."""
        orch = _FakeOrchestrator([[], ["http_fetch"]])
        result, success = await _execute_agent_task(_services(orch), _task())

        assert success is False
        assert len(orch.calls) == 1
        assert "not applied" in result
        assert "http_fetch" in result

    async def test_reports_missing_tool_after_all_attempts(self):
        """Tool never bound -> fail the single durable scheduler turn."""
        orch = _FakeOrchestrator([[]])
        result, success = await _execute_agent_task(_services(orch), _task())

        assert success is False
        assert len(orch.calls) == 1
        assert "not applied" in result
        assert "http_fetch" in result

    async def test_empty_allowlist_requires_no_tools(self):
        """No declared tools -> no enforcement, single run, no note."""
        orch = _FakeOrchestrator([[]])
        result, success = await _execute_agent_task(
            _services(orch), _task(allowed="[]")
        )

        assert success is True
        assert len(orch.calls) == 1
        assert "not applied" not in result

    async def test_delivers_result_through_task_chat_bridge(self):
        """Result is forwarded via the sched_ chat's bridge under the real id."""
        orch = _FakeOrchestrator([["http_fetch"]])
        services = _services(orch)
        bridge = MagicMock()
        bridge.sync_outbound_message = AsyncMock()
        services.bridge_service = bridge

        await _execute_agent_task(services, _task())

        bridge.sync_outbound_message.assert_awaited_once()
        kwargs = bridge.sync_outbound_message.await_args.kwargs
        # The task's own conversation is the delivery target, NOT the origin.
        assert kwargs["conversation_id"] == "sched_t1"
        # The real persisted assistant message id is used (dedup on reconnect),
        # not a synthetic "sched_msg_..." id.
        assert kwargs["message_id"] == "msg-0"
        assert kwargs["role"] == "assistant"
        # Agent-task results go out bare — no "[Scheduled task] ..." label.
        assert kwargs["content"] == "Done."

    async def test_scheduled_mode_and_allowlist_active_during_run(self):
        """Scheduled flag + allowlist are set while running, restored after."""
        orch = _FakeOrchestrator([["http_fetch"]])
        await _execute_agent_task(_services(orch), _task())

        # Active during the run...
        assert orch.mode_during_calls == [True]
        assert orch.allowed_during_calls == [{"http_fetch"}]
        # ...restored to the interactive defaults afterwards.
        assert orch.prompt_builder.scheduled_task_mode is False
        assert orch.prompt_builder.allowed_tool_names is None
        assert orch.tool_executor.allowed_tool_names is None
