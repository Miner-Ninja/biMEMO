"""Integration-style tests for confirmed action resume flow."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
ORCH_ROOT = ROOT / "app" / "backend" / "processes" / "orchestrator"


def _load_orchestrator_process() -> types.ModuleType:
    """Load orchestrator process module without importing package __init__."""
    package = types.ModuleType("backend.processes.orchestrator")
    package.__path__ = [str(ORCH_ROOT)]
    sys.modules["backend.processes.orchestrator"] = package
    sys.modules["backend.processes.orchestrator.client"] = SimpleNamespace(
        DBProxyClient=object,
        DBProxyError=Exception,
    )
    sys.modules["backend.processes.orchestrator.ipc_server"] = SimpleNamespace(
        OrchestratorIPCServer=object,
    )
    sys.modules["backend.processes.orchestrator.process_helpers"] = SimpleNamespace(
        current_processing_timestamp=lambda: "2026-04-25T10:00:00.000",
        init_stt_client=lambda *args, **kwargs: None,
        init_tts_client=lambda *args, **kwargs: None,
        resolve_user_tz=lambda *args, **kwargs: None,
    )
    sys.modules["backend.processes.orchestrator.process_models"] = SimpleNamespace(
        RuntimeServices=object,
        RuntimeStartupState=object,
    )

    module_name = "backend.processes.orchestrator.process"
    spec = importlib.util.spec_from_file_location(module_name, ORCH_ROOT / "process.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class FakeDBClient:
    """Minimal DB proxy client for confirmed action resume tests."""

    def __init__(self, snapshot: dict[str, object] | None) -> None:
        self.snapshot = snapshot
        self.completed: list[dict[str, object]] = []
        self.messages: list[dict[str, object]] = []

    async def get_agent_run(self, run_id: str) -> dict[str, object]:
        """Return one persisted run."""
        return {
            "id": run_id,
            "conversation_id": "conv-1",
            "session_id": "session-1",
            "channel": "web",
        }

    async def get_resume_snapshot(
        self,
        *,
        action_id: str = "",
        snapshot_id: str = "",
    ) -> dict[str, object] | None:
        """Return one runtime resume snapshot."""
        if self.snapshot is None:
            return None
        return {
            "id": snapshot_id,
            "action_id": action_id,
            "snapshot": self.snapshot,
        }

    async def complete_pending_action(self, **kwargs: object) -> dict[str, object]:
        """Record pending action completion."""
        self.completed.append(kwargs)
        return {"accepted": True}

    async def save_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        channel: str = "",
        meta: str = "",
    ) -> str:
        """Record one saved assistant message."""
        self.messages.append(
            {
                "conversation_id": conversation_id,
                "role": role,
                "content": content,
                "channel": channel,
                "meta": meta,
            }
        )
        return "msg-1"


class FakeBroker:
    """Minimal tool broker for confirmed action resume tests."""

    def __init__(self) -> None:
        self.context: dict[str, str] = {}
        self.executed: list[tuple[str, dict[str, object]]] = []
        self.cleared = False

    def set_context(self, context: dict[str, str]) -> None:
        """Record broker context."""
        self.context = context

    async def execute(
        self,
        tool_name: str,
        parameters: dict[str, object],
    ) -> object:
        """Return a successful tool result."""
        self.executed.append((tool_name, parameters))
        return SimpleNamespace(
            success=True,
            result="tool result text",
            tool_name=tool_name,
            zone=SimpleNamespace(value="red"),
        )

    def clear_context(self) -> None:
        """Record context cleanup."""
        self.cleared = True


@pytest.mark.asyncio
async def test_confirmed_action_executes_tool_and_resumes_from_runtime_snapshot() -> None:
    """Approved action should execute, resume LLM, and save final message."""
    process = _load_orchestrator_process()
    db_client = FakeDBClient(
        {
            "tool_call_id": "call-1",
            "messages": [{"role": "user", "content": "send it"}],
            "assistant_message": {"role": "assistant", "tool_calls": []},
        }
    )
    broker = FakeBroker()
    llm_calls: list[list[dict[str, object]]] = []

    async def llm_call(
        messages: list[dict[str, object]],
        tools_override: list[object] | None = None,
    ) -> object:
        assert tools_override == []
        llm_calls.append(messages)
        return SimpleNamespace(
            structured_content=SimpleNamespace(answer="final assistant answer"),
            text="",
        )

    result = await process._execute_confirmed_action(
        broker=broker,
        db_client=db_client,
        stored_action={
            "id": "action-1",
            "run_id": "run-1",
            "tool_name": "email_send",
            "parameters": {"to": "a@example.com"},
            "channel": "web",
            "preview": {"resume_snapshot_id": "snapshot-1"},
        },
        llm_call=llm_call,
    )

    assert result["status"] == "completed"
    assert result["message_id"] == "msg-1"
    assert result["result_summary"] == "final assistant answer"
    assert broker.executed == [("email_send", {"to": "a@example.com"})]
    assert broker.context["agent_run_id"] == "run-1"
    assert broker.cleared is True
    assert db_client.completed[0]["status"] == "completed"
    assert db_client.completed[0]["result"]["result"] == "tool result text"
    assert llm_calls[0][-1] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": "tool result text",
    }
    assert "final assistant answer" in db_client.messages[0]["content"]


@pytest.mark.asyncio
async def test_confirmed_action_fails_before_tool_when_runtime_snapshot_missing() -> None:
    """Missing runtime snapshot should fail visibly before tool execution."""
    process = _load_orchestrator_process()
    db_client = FakeDBClient(snapshot=None)
    broker = FakeBroker()

    async def llm_call(
        messages: list[dict[str, object]],
        tools_override: list[object] | None = None,
    ) -> object:
        raise AssertionError("LLM should not be called without resume snapshot")

    result = await process._execute_confirmed_action(
        broker=broker,
        db_client=db_client,
        stored_action={
            "id": "action-1",
            "run_id": "run-1",
            "tool_name": "email_send",
            "parameters": {"to": "a@example.com"},
            "channel": "web",
            "preview": {"resume_snapshot_id": "missing"},
        },
        llm_call=llm_call,
    )

    assert result["status"] == "failed"
    assert broker.executed == []
    assert db_client.completed[0]["status"] == "failed"
    assert "resume snapshot is no longer available" in db_client.messages[0]["content"]
