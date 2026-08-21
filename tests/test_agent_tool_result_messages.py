"""Tests for building role:tool result messages from tool calls."""

import json

from backend.agent.agent_loop_helpers import build_tool_result_messages
from backend.agent.agent_loop_models import ToolCall
from backend.tools.broker import ToolResult


class TestBuildToolResultMessages:
    """Tests for tool result message building."""

    def test_success_result(self):
        """Verify success result produces role:tool message."""
        calls = [ToolCall(id="call_1", name="file_read", parameters={})]
        results = [
            ToolResult(
                tool_name="file_read",
                success=True,
                result="content",
                duration_ms=1.0,
            )
        ]
        msgs = build_tool_result_messages(calls, results)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "tool"
        assert msgs[0]["tool_call_id"] == "call_1"
        assert json.loads(msgs[0]["content"]) == {
            "data": "content",
            "invocation_id": "call_1",
            "original_bytes": 7,
            "source": "file_read",
            "truncated": False,
        }

    def test_error_result(self):
        """Verify error result produces role:tool message."""
        calls = [ToolCall(id="call_2", name="file_read", parameters={})]
        results = [
            ToolResult(
                tool_name="file_read",
                success=False,
                result="not found",
                duration_ms=1.0,
            )
        ]
        msgs = build_tool_result_messages(calls, results)
        assert json.loads(msgs[0]["content"]) == {
            "data": "not found",
            "invocation_id": "call_2",
            "original_bytes": 9,
            "source": "file_read",
            "truncated": False,
        }

    def test_multiple_results(self):
        """Verify multiple results produce matching messages."""
        calls = [
            ToolCall(id="call_a", name="a", parameters={}),
            ToolCall(id="call_b", name="b", parameters={}),
        ]
        results = [
            ToolResult(
                tool_name="a",
                success=True,
                result="r1",
                duration_ms=1.0,
            ),
            ToolResult(
                tool_name="b",
                success=True,
                result="r2",
                duration_ms=1.0,
            ),
        ]
        msgs = build_tool_result_messages(calls, results)
        assert len(msgs) == 2
        assert msgs[0]["tool_call_id"] == "call_a"
        assert msgs[1]["tool_call_id"] == "call_b"
