"""Behaviour contract tests for the admin turn-trace assembly (F27).

The trace endpoint stitches one agent run together from persisted tables.
The contract tested here: message-content flattening, the user/assistant
message selection rules relative to the run start time, run-list assembly
with conversation titles and 200-char request previews, and the trace
assembly that splits events into tool calls and the four signal types.
"""

from __future__ import annotations

from typing import Any

from backend.processes.fastapi_app.routes.admin_trace import (
    _latest_user_before,
    _message_text,
    _turn_messages,
    build_run_list,
    build_turn_trace,
    get_turn_trace,
    list_recent_traces,
)


class _FakeAdminClient:
    """Socket B admin client stand-in with canned tables."""

    def __init__(self, **tables: Any) -> None:
        self.tables = tables
        self.message_queries: list[str] = []

    async def list_agent_runs(self, limit: int = 20):
        return self.tables.get("runs", [])

    async def list_conversations(self, limit: int = 200):
        return self.tables.get("conversations", [])

    async def list_messages(self, conversation_id: str, limit: int = 200):
        self.message_queries.append(conversation_id)
        return self.tables.get("messages", {}).get(conversation_id, [])

    async def get_agent_run(self, run_id: str):
        runs = {run["id"]: run for run in self.tables.get("runs", [])}
        return runs.get(run_id)

    async def list_agent_run_events(self, run_id: str):
        return self.tables.get("events", {}).get(run_id, [])

    async def list_agent_confirmations(self, run_id: str):
        return self.tables.get("confirmations", {}).get(run_id, [])


RUN_1 = {
    "id": "run-1",
    "conversation_id": "conv-1",
    "channel": "web",
    "status": "completed",
    "created_at": "2026-08-01T10:00:00+00:00",
}

MESSAGES = {
    "conv-1": [
        {
            "role": "user",
            "content": "earlier question",
            "created_at": "2026-08-01T09:00:00+00:00",
        },
        {
            "role": "user",
            "content": "the request",
            "created_at": "2026-08-01T09:59:00+00:00",
        },
        {
            "role": "assistant",
            "content": "the answer",
            "created_at": "2026-08-01T10:00:30+00:00",
        },
        {
            "role": "assistant",
            "content": "second answer",
            "created_at": "2026-08-01T10:01:00+00:00",
        },
    ],
}


class TestMessageText:
    """S1: flattening stored message content into plain text."""

    def test_plain_string_is_returned_unchanged(self):
        """String content is the text."""
        assert _message_text("hello") == "hello"

    def test_content_parts_are_joined(self):
        """Text parts of a content list are joined with spaces."""
        content = [
            {"type": "text", "text": "one"},
            {"type": "image", "url": "http://x"},
            {"type": "text", "text": "two"},
        ]
        assert _message_text(content) == "one two"

    def test_empty_text_parts_are_dropped(self):
        """Empty text parts do not leave double spaces."""
        content = [
            {"type": "text", "text": ""},
            {"type": "text", "text": "kept"},
        ]
        assert _message_text(content) == "kept"

    def test_unknown_content_type_is_empty(self):
        """Anything that is not a string or a list yields no text."""
        assert _message_text({"unexpected": 1}) == ""
        assert _message_text(None) == ""


class TestLatestUserBefore:
    """S2: picking the user request that triggered a run."""

    def test_selects_the_newest_user_message_at_or_before_start(self):
        """Of the user messages not after the run start, the newest wins."""
        message = _latest_user_before(MESSAGES["conv-1"], RUN_1["created_at"])
        assert message is not None
        assert message["content"] == "the request"

    def test_message_exactly_at_start_is_included(self):
        """The boundary is inclusive: created_at == started_at counts."""
        at_start = {
            "role": "user",
            "content": "boundary",
            "created_at": "2026-08-01T10:00:00+00:00",
        }
        message = _latest_user_before([at_start], at_start["created_at"])
        assert message is at_start

    def test_no_user_message_is_none(self):
        """Assistant-only conversations produce no user message."""
        only_assistant = [
            {
                "role": "assistant",
                "content": "x",
                "created_at": "2026-08-01T09:00:00+00:00",
            }
        ]
        assert _latest_user_before(only_assistant, "2026-08-01T10:00:00+00:00") is None


class TestBuildRunList:
    """The picker list: runs with titles and request previews."""

    async def test_attaches_title_and_truncated_user_message(self):
        """Each run carries its conversation title and a 200-char preview."""
        long_message = {
            "role": "user",
            "content": "x" * 500,
            "created_at": "2026-08-01T09:59:00+00:00",
        }
        client = _FakeAdminClient(
            runs=[RUN_1],
            conversations=[{"id": "conv-1", "title": "My chat"}],
            messages={"conv-1": [long_message]},
        )
        items = await build_run_list(admin_client=client, limit=10)
        assert len(items) == 1
        item = items[0]
        assert item["conversation_title"] == "My chat"
        assert item["user_message"] == "x" * 200
        assert item["status"] == "completed"

    async def test_message_lookup_is_cached_per_conversation(self):
        """Two runs of one conversation trigger one message query."""
        client = _FakeAdminClient(
            runs=[
                RUN_1,
                {**RUN_1, "id": "run-2", "created_at": "2026-08-01T11:00:00+00:00"},
            ],
            conversations=[],
            messages={"conv-1": []},
        )
        await build_run_list(admin_client=client, limit=10)
        assert client.message_queries == ["conv-1"]

    async def test_run_without_conversation_has_empty_preview(self):
        """A run outside a conversation still appears, with no request text."""
        client = _FakeAdminClient(
            runs=[{**RUN_1, "id": "run-3", "conversation_id": ""}],
            conversations=[],
        )
        items = await build_run_list(admin_client=client, limit=10)
        assert items[0]["user_message"] == ""
        assert client.message_queries == []


class TestTurnMessages:
    """The user request and assistant answer of one turn."""

    async def test_no_conversation_yields_both_none(self):
        """A run without a conversation has no messages."""
        client = _FakeAdminClient()
        result = await _turn_messages(
            admin_client=client,
            conversation_id="",
            run=RUN_1,
        )
        assert result == {"user": None, "assistant": None}

    async def test_user_is_latest_before_start_and_assistant_earliest_after(self):
        """The turn pairs the user request with the run's answer.

        The user side is the newest user message at or before the run
        start; the assistant side is the earliest one after it.
        """
        client = _FakeAdminClient(messages=MESSAGES)
        result = await _turn_messages(
            admin_client=client,
            conversation_id="conv-1",
            run=RUN_1,
        )
        assert result["user"]["content"] == "the request"
        assert result["assistant"]["content"] == "the answer"
        assert result["user"]["created_at"] == "2026-08-01T09:59:00+00:00"

    async def test_missing_assistant_is_none(self):
        """A run whose answer was never stored has no assistant side."""
        only_user = [
            {
                "role": "user",
                "content": "the request",
                "created_at": "2026-08-01T09:59:00+00:00",
            }
        ]
        client = _FakeAdminClient(messages={"conv-1": only_user})
        result = await _turn_messages(
            admin_client=client,
            conversation_id="conv-1",
            run=RUN_1,
        )
        assert result["user"] is not None
        assert result["assistant"] is None


class TestBuildTurnTrace:
    """The full trace document for one run."""

    async def test_absent_run_is_none(self):
        """An unknown run id yields no trace, not an error."""
        client = _FakeAdminClient(runs=[])
        assert await build_turn_trace(admin_client=client, run_id="missing") is None

    async def test_trace_splits_events_into_tools_and_signals(self):
        """Events are split by their recorded type.

        Tool calls are the tool_status events; signals maps the four
        signal event types to their payloads.
        """
        events = [
            {"event_type": "tool_status", "payload": {"tool": "search"}},
            {"event_type": "tool_status", "payload": {"tool": "read"}},
            {"event_type": "forced_tools", "payload": ["search"]},
            {"event_type": "requirements", "payload": ["r1"]},
            {"event_type": "tool_plan", "payload": ["plan"]},
            {"event_type": "reask_decision", "payload": "no"},
            {"event_type": "noise_event", "payload": "x"},
        ]
        client = _FakeAdminClient(
            runs=[RUN_1],
            messages=MESSAGES,
            events={"run-1": events},
            confirmations={"run-1": [{"id": "c1"}]},
        )
        trace = await build_turn_trace(admin_client=client, run_id="run-1")
        assert trace is not None
        assert trace["run"]["id"] == "run-1"
        assert trace["conversation_id"] == "conv-1"
        assert len(trace["tool_calls"]) == 2
        assert set(trace["signals"]) == {
            "forced_tools",
            "requirements",
            "tool_plan",
            "reask_decision",
        }
        assert trace["signals"]["reask_decision"] == "no"
        assert trace["confirmations"] == [{"id": "c1"}]
        assert len(trace["events"]) == len(events)
        assert trace["messages"]["user"]["content"] == "the request"


class TestTraceRoutes:
    """The two HTTP entry points above the assembly helpers."""

    async def test_list_recent_traces_returns_runs_key(self):
        """GET /admin/trace returns the picker list under ``runs``."""
        client = _FakeAdminClient(runs=[RUN_1])
        result = await list_recent_traces(admin=client, limit=10)
        assert list(result["runs"][0].keys()) == [
            "id",
            "conversation_id",
            "conversation_title",
            "channel",
            "status",
            "created_at",
            "user_message",
        ]

    async def test_get_turn_trace_returns_trace(self):
        """GET /admin/trace/{run_id} returns the assembled trace."""
        client = _FakeAdminClient(runs=[RUN_1])
        trace = await get_turn_trace(run_id="run-1", admin=client)
        assert trace["run"]["id"] == "run-1"
