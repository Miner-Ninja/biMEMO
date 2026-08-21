"""Tests for the chat stream-event plumbing.

Covers the conversation-updated event push, the processing-state
transitions, the request-event forwarder (event-type mapping and
unknown-type skipping), and the final event payload built from the
IPC chat response.
"""

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock


if TYPE_CHECKING:
    import pytest

from backend.ipc.models import (
    ChatResponse as IPCChatResponse,
)
from backend.ipc.models import (
    StreamEvent,
    StreamEventType,
)
from backend.ipc.models import (
    StructuredAssistantContent as IPCStructuredAssistantContent,
)
from backend.processes.orchestrator.chat_handler import (
    _chat_response_final_event_data,
    _forward_request_events_to_ipc,
    _mark_conversation_processing_active,
    _mark_conversation_processing_idle,
    _push_conversation_updated_event,
)


class _FakeIPCServer:
    """Minimal IPC server stand-in that records pushed events."""

    def __init__(self) -> None:
        self.events: list[StreamEvent] = []

    async def push_event(self, event: StreamEvent) -> None:
        """Record one event for later assertions."""
        self.events.append(event)


def test_push_conversation_updated_event_with_bound_queue() -> None:
    """The helper pushes a formatted dict onto the bound stream queue."""
    queue: asyncio.Queue = asyncio.Queue()
    orch = SimpleNamespace(stream_queue=queue)

    asyncio.run(
        _push_conversation_updated_event(
            orch=orch,
            conversation_id="conv-1",
            processing_status="active",
            processing_stage="Processing...",
            processing_started_at="2026-04-24T20:00:00.000",
        )
    )

    assert queue.qsize() == 1
    event = queue.get_nowait()
    assert event == {
        "type": "conversation_updated",
        "data": {
            "conversation_id": "conv-1",
            "processing_status": "active",
            "processing_stage": "Processing...",
            "processing_started_at": "2026-04-24T20:00:00.000",
        },
    }


def test_push_conversation_updated_event_no_queue_is_noop() -> None:
    """Helper returns silently when orch has no bound stream queue."""
    orch = SimpleNamespace(stream_queue=None)

    asyncio.run(
        _push_conversation_updated_event(
            orch=orch,
            conversation_id="conv-1",
            processing_status="idle",
            processing_stage="",
            processing_started_at=None,
        )
    )


def test_mark_conversation_processing_idle_emits_sse_event() -> None:
    """Marking idle writes to DB and pushes a conversation_updated event."""
    queue: asyncio.Queue = asyncio.Queue()
    orch = SimpleNamespace(stream_queue=queue)
    db_client = SimpleNamespace(
        update_conversation_processing_state=AsyncMock(return_value=None)
    )

    asyncio.run(
        _mark_conversation_processing_idle(
            db_client=db_client,
            orch=orch,
            conversation_id="conv-42",
        )
    )

    db_client.update_conversation_processing_state.assert_awaited_once_with(
        "conv-42",
        "idle",
        processing_stage="",
        processing_started_at=None,
    )
    assert queue.qsize() == 1
    event = queue.get_nowait()
    assert event["type"] == "conversation_updated"
    assert event["data"] == {
        "conversation_id": "conv-42",
        "processing_status": "idle",
        "processing_stage": "",
        "processing_started_at": None,
    }


def test_mark_conversation_processing_active_emits_sse_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Marking active writes to DB and pushes a conversation_updated event."""
    monkeypatch.setattr(
        "backend.processes.orchestrator.process_helpers.current_processing_timestamp",
        lambda: "2026-04-25T10:00:00.000",
    )
    queue: asyncio.Queue = asyncio.Queue()
    orch = SimpleNamespace(stream_queue=queue)
    db_client = SimpleNamespace(
        update_conversation_processing_state=AsyncMock(return_value=None)
    )

    asyncio.run(
        _mark_conversation_processing_active(
            db_client=db_client,
            orch=orch,
            conversation_id="conv-7",
            processing_stage="Processing...",
        )
    )

    db_client.update_conversation_processing_state.assert_awaited_once_with(
        "conv-7",
        "active",
        processing_stage="Processing...",
        processing_started_at="2026-04-25T10:00:00.000",
    )
    assert queue.qsize() == 1
    event = queue.get_nowait()
    assert event["data"] == {
        "conversation_id": "conv-7",
        "processing_status": "active",
        "processing_stage": "Processing...",
        "processing_started_at": "2026-04-25T10:00:00.000",
    }


def test_forwarder_maps_conversation_updated_to_stream_event_type() -> None:
    """The forwarder turns a conversation_updated dict into a stream event."""
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(
        {
            "type": "conversation_updated",
            "data": {
                "conversation_id": "conv-9",
                "processing_status": "idle",
                "processing_stage": "",
                "processing_started_at": None,
            },
        }
    )
    queue.put_nowait({"type": "done"})
    ipc_server = _FakeIPCServer()

    asyncio.run(
        _forward_request_events_to_ipc(
            request_id="req-1",
            request_stream_queue=queue,
            ipc_server=ipc_server,
        )
    )

    assert [event.event_type for event in ipc_server.events] == [
        StreamEventType.CONVERSATION_UPDATED,
        StreamEventType.DONE,
    ]
    conversation_event = ipc_server.events[0]
    assert conversation_event.request_id == "req-1"
    assert conversation_event.data == {
        "conversation_id": "conv-9",
        "processing_status": "idle",
        "processing_stage": "",
        "processing_started_at": None,
    }


def test_forwarder_maps_confirmation_to_stream_event_type() -> None:
    """The forwarder passes confirmation lifecycle events through."""
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(
        {
            "type": "confirmation",
            "status": "waiting_confirmation",
            "action_id": "act-1",
        }
    )
    queue.put_nowait({"type": "done"})
    ipc_server = _FakeIPCServer()

    asyncio.run(
        _forward_request_events_to_ipc(
            request_id="req-3",
            request_stream_queue=queue,
            ipc_server=ipc_server,
        )
    )

    assert [event.event_type for event in ipc_server.events] == [
        StreamEventType.CONFIRMATION,
        StreamEventType.DONE,
    ]
    assert ipc_server.events[0].data["status"] == "waiting_confirmation"
    assert ipc_server.events[0].data["action_id"] == "act-1"


def test_forwarder_maps_final_to_stream_event_type() -> None:
    """The forwarder passes final assistant payload events through."""
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(
        {
            "type": "final",
            "conversation_id": "conv-1",
            "structured_content": {"answer": "Done", "suggestions": []},
            "pending_confirmations": [],
        }
    )
    queue.put_nowait({"type": "done"})
    ipc_server = _FakeIPCServer()

    asyncio.run(
        _forward_request_events_to_ipc(
            request_id="req-final",
            request_stream_queue=queue,
            ipc_server=ipc_server,
        )
    )

    assert [event.event_type for event in ipc_server.events] == [
        StreamEventType.FINAL,
        StreamEventType.DONE,
    ]
    assert ipc_server.events[0].data["type"] == "final"
    assert ipc_server.events[0].data["structured_content"]["answer"] == "Done"


def test_forwarder_maps_error_to_stream_event_type() -> None:
    """The forwarder passes terminal stream errors through."""
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(
        {
            "type": "error",
            "conversation_id": "conv-1",
            "message": "Request failed",
        }
    )
    queue.put_nowait({"type": "done"})
    ipc_server = _FakeIPCServer()

    asyncio.run(
        _forward_request_events_to_ipc(
            request_id="req-error",
            request_stream_queue=queue,
            ipc_server=ipc_server,
        )
    )

    assert [event.event_type for event in ipc_server.events] == [
        StreamEventType.ERROR,
        StreamEventType.DONE,
    ]
    assert ipc_server.events[0].data["type"] == "error"
    assert ipc_server.events[0].data["message"] == "Request failed"


def test_chat_response_final_event_data_mirrors_chat_payload() -> None:
    """Build the stream-final event from the same IPC chat response fields."""
    chat_response = IPCChatResponse(
        request_id="request-final",
        structured_content=IPCStructuredAssistantContent(
            answer="Visible answer",
            thinking="Internal plan",
            suggestions=["Next step"],
        ),
        input_filtered=False,
        output_filtered=True,
        pending_confirmations=[
            {
                "action_id": "action-1",
                "tool_name": "http_fetch",
                "parameters": {"url": "https://example.com"},
            }
        ],
        retrieved_memories=[{"summary": "Memory", "date": "2026-05-10"}],
        retrieved_messages=[{"role": "user", "content": "Earlier"}],
        transcription="voice text",
        stats=None,
        stream_enabled=True,
    )

    event_data = _chat_response_final_event_data(
        conversation_id="conversation-1",
        response=chat_response,
    )

    assert event_data == {
        "type": "final",
        "conversation_id": "conversation-1",
        "input_filtered": False,
        "output_filtered": True,
        "pending_confirmations": [
            {
                "action_id": "action-1",
                "tool_name": "http_fetch",
                "parameters": {"url": "https://example.com"},
            }
        ],
        "pause_reason": None,
        "pending_input": None,
        "retrieved_memories": [{"summary": "Memory", "date": "2026-05-10"}],
        "retrieved_messages": [{"role": "user", "content": "Earlier"}],
        "transcription": "voice text",
        "stats": None,
        "structured_content": {
            "answer": "Visible answer",
            "thinking": "Internal plan",
            "suggestions": ["Next step"],
        },
        "stream_enabled": True,
    }


def test_forwarder_skips_unknown_event_types() -> None:
    """Unknown event types do not abort the forwarder loop."""
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait({"type": "totally_bogus", "data": {}})
    queue.put_nowait({"type": "done"})
    ipc_server = _FakeIPCServer()

    asyncio.run(
        _forward_request_events_to_ipc(
            request_id="req-2",
            request_stream_queue=queue,
            ipc_server=ipc_server,
        )
    )

    assert [event.event_type for event in ipc_server.events] == [
        StreamEventType.DONE,
    ]
