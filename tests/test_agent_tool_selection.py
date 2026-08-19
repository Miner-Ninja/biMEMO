"""Tests for select_openai_tools in PromptBuilder.

Covers forced file tool inclusion via required_names and registry filtering.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.agent.agent_loop import AgentOrchestrator
from backend.agent.prompt_builder import (
    FILE_READ_TOOL_NAMES,
    FILE_WRITE_TOOL_NAME,
    PromptBuilder,
)
from backend.tools.broker import ToolBroker
from backend.tools.registry import ToolRegistry


def _builder(*, allowed_tool_names=None, registry_tools=None):
    """Build a PromptBuilder with a mock broker."""
    registry = ToolRegistry()
    if registry_tools is not None:
        for name in registry_tools:
            registry._tools[name] = MagicMock()
    broker = MagicMock(spec=ToolBroker)
    broker.registry = registry
    broker.list_openai_schemas.return_value = []

    pb = PromptBuilder(
        model_name="test",
        user_profile={},
        broker=broker,
        evidence_bundle_fn=None,
    )
    pb.allowed_tool_names = allowed_tool_names
    return pb


def test_forced_file_write_tool_via_required_names():
    """Forced file_write via required_names survives heuristic exclusion."""
    registry_tools = ["file_write", "http_fetch", "web_search"]
    pb = _builder(registry_tools=registry_tools)
    pb.select_openai_tools(
        "Hello, how are you?",
        required_names={FILE_WRITE_TOOL_NAME},
    )

    call_args = pb.broker.list_openai_schemas.call_args
    allowed = call_args.kwargs["allowed_names"]
    assert FILE_WRITE_TOOL_NAME in allowed


def test_forced_file_read_tools_via_required_names():
    """Forced file read tools via required_names survive heuristic exclusion."""
    registry_tools = ["file_read", "file_search", "http_fetch"]
    pb = _builder(registry_tools=registry_tools)
    pb.select_openai_tools(
        "Hello, how are you?",
        required_names=FILE_READ_TOOL_NAMES,
    )

    call_args = pb.broker.list_openai_schemas.call_args
    allowed = call_args.kwargs["allowed_names"]
    assert "file_read" in allowed
    assert "file_search" in allowed


def test_unknown_required_tool_name_is_silently_ignored():
    """A required_names entry not in the registry is silently ignored."""
    registry_tools = ["http_fetch", "web_search"]
    pb = _builder(registry_tools=registry_tools)
    result = pb.select_openai_tools(
        "Hello",
        required_names={"nonexistent_tool"},
    )

    call_args = pb.broker.list_openai_schemas.call_args
    allowed = call_args.kwargs["allowed_names"]
    assert "nonexistent_tool" not in allowed
    assert result == []


def test_unknown_required_tool_with_scheduler_allowlist():
    """An unknown required tool is dropped by the scheduler allowlist."""
    registry_tools = ["http_fetch", "web_search"]
    pb = _builder(
        registry_tools=registry_tools,
        allowed_tool_names={"http_fetch", "web_search"},
    )
    pb.select_openai_tools(
        "Hello",
        required_names={"nonexistent_tool"},
    )

    call_args = pb.broker.list_openai_schemas.call_args
    allowed = call_args.kwargs["allowed_names"]
    # Scheduler allowlist intersection removes the unknown name
    assert "nonexistent_tool" not in allowed
    assert allowed == {"http_fetch", "web_search"}


def test_structured_direct_answer_exposes_no_optional_tools():
    """A structured greeting must not become tool-capable by registry default."""
    pb = _builder(registry_tools=["web_search", "memory_recall", "http_fetch"])

    result = pb.select_openai_tools(
        "Привет",
        required_names=set(),
        required_only=True,
    )

    assert result == []
    assert pb.broker.list_openai_schemas.call_args.kwargs["allowed_names"] == set()


def test_structured_evidence_turn_exposes_only_required_schema():
    """An evidence turn does not offer unrelated optional actions."""
    pb = _builder(registry_tools=["web_search", "memory_recall", "http_fetch"])

    pb.select_openai_tools(
        "Найди актуальные данные",
        required_names={"web_search"},
        required_only=True,
    )

    assert pb.broker.list_openai_schemas.call_args.kwargs["allowed_names"] == {
        "web_search"
    }


async def test_unknown_forced_tool_is_rejected_before_model_call() -> None:
    """Unknown explicit names fail before concurrency or provider access."""
    orchestrator = object.__new__(AgentOrchestrator)
    orchestrator.broker = SimpleNamespace(
        registry=SimpleNamespace(tool_names=["file_read"])
    )

    with pytest.raises(ValueError, match="Unknown forced tool"):
        await orchestrator.process_message(
            message="read",
            channel="chat",
            llm_call=MagicMock(),
            conversation_id="conversation-1",
            forced_tools=["not_registered"],
        )
