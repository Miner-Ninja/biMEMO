"""Tests for IPC message models serialization and validation.

Covers ChatRequest, ChatResponse, StreamEvent, DBOperation, DBResult,
and all other inter-process message types. Verifies extra="forbid"
rejects unknown fields and required fields are enforced.
"""

import pytest
from backend.ipc.models import (
    ChatRequest,
    ChatResponse,
    ConfirmRequest,
    ConfirmResponse,
    DBOperation,
    DBResult,
    StopRequest,
    StopResponse,
    StreamEvent,
    StreamEventType,
    StructuredAssistantContent,
)
from pydantic import ValidationError


class TestChatRequest:
    """Tests for ChatRequest model."""

    def test_valid_chat_request(self):
        """Create a valid ChatRequest with all fields."""
        req = ChatRequest(
            request_id="req-1",
            message="Hello",
            channel="web",
            session_id="sess-1",
            conversation_id="conv-1",
        )
        assert req.request_id == "req-1"
        assert req.message == "Hello"

    def test_json_roundtrip(self):
        """Serialize to JSON and deserialize back."""
        req = ChatRequest(
            request_id="req-1",
            message="Hello",
            channel="web",
            session_id="sess-1",
            conversation_id="conv-1",
        )
        json_str = req.model_dump_json()
        restored = ChatRequest.model_validate_json(json_str)
        assert restored == req

    def test_extra_field_rejected(self):
        """Reject unknown fields due to extra='forbid'."""
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            ChatRequest(
                request_id="req-1",
                message="Hello",
                channel="web",
                session_id="sess-1",
                conversation_id="conv-1",
                unknown_field="bad",
            )

    def test_missing_required_field(self):
        """Raise error when required field is missing."""
        with pytest.raises(ValidationError):
            ChatRequest(
                request_id="req-1",
                message="Hello",
            )


class TestStopRequest:
    """Tests for StopRequest model."""

    def test_valid(self):
        """Create a valid StopRequest."""
        req = StopRequest(request_id="req-1")
        assert req.request_id == "req-1"

    def test_json_roundtrip(self):
        """Serialize to JSON and deserialize back."""
        req = StopRequest(request_id="req-1")
        restored = StopRequest.model_validate_json(req.model_dump_json())
        assert restored == req


class TestConfirmRequest:
    """Tests for ConfirmRequest model."""

    def test_valid(self):
        """Create a valid ConfirmRequest."""
        req = ConfirmRequest(
            request_id="req-1",
            action_id="act-1",
            decision="approve",
        )
        assert req.decision == "approve"

    def test_extra_field_rejected(self):
        """Reject unknown fields."""
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            ConfirmRequest(
                request_id="req-1",
                action_id="act-1",
                decision="approve",
                extra="bad",
            )


class TestChatResponse:
    """Tests for ChatResponse model."""

    def test_valid_minimal(self):
        """Create a ChatResponse with only required fields."""
        resp = ChatResponse(
            request_id="req-1",
            structured_content=StructuredAssistantContent(answer="Hi there"),
        )
        assert resp.input_filtered is False
        assert resp.output_filtered is False
        assert resp.pending_confirmations is None
        assert resp.stats is None

    def test_valid_full(self):
        """Create a ChatResponse with all fields."""
        resp = ChatResponse(
            request_id="req-1",
            structured_content=StructuredAssistantContent(answer="Hi"),
            input_filtered=True,
            output_filtered=False,
            pending_confirmations=[{"action_id": "a1", "tool": "email_send"}],
        )
        assert resp.input_filtered is True
        assert len(resp.pending_confirmations) == 1

    def test_json_roundtrip(self):
        """Serialize to JSON and deserialize back."""
        resp = ChatResponse(
            request_id="req-1",
            structured_content=StructuredAssistantContent(answer="Hi"),
        )
        restored = ChatResponse.model_validate_json(resp.model_dump_json())
        assert restored == resp

    def test_structured_content_roundtrip(self):
        """Serialize structured assistant content inside ChatResponse."""
        resp = ChatResponse(
            request_id="req-1",
            structured_content=StructuredAssistantContent(
                answer="Hi",
                sql="SELECT 1",
                suggestions=["Next step"],
            ),
        )
        restored = ChatResponse.model_validate_json(resp.model_dump_json())
        assert restored.structured_content is not None
        assert restored.structured_content.answer == "Hi"
        assert restored.structured_content.sql == "SELECT 1"


class TestStopResponse:
    """Tests for StopResponse model."""

    def test_valid(self):
        """Create a valid StopResponse."""
        resp = StopResponse(request_id="req-1", stopped=True)
        assert resp.stopped is True


class TestConfirmResponse:
    """Tests for ConfirmResponse model."""

    def test_valid(self):
        """Create a valid ConfirmResponse."""
        resp = ConfirmResponse(
            request_id="req-1",
            action_id="act-1",
            accepted=True,
        )
        assert resp.accepted is True


class TestStreamEvent:
    """Tests for StreamEvent model."""

    def test_token_event(self):
        """Create a token streaming event."""
        event = StreamEvent(
            request_id="req-1",
            event_type=StreamEventType.TOKEN,
            data="Hello",
        )
        assert event.event_type == StreamEventType.TOKEN
        assert event.data == "Hello"

    def test_tool_status_event(self):
        """Create a tool status streaming event."""
        event = StreamEvent(
            request_id="req-1",
            event_type=StreamEventType.TOOL_STATUS,
            data={"tool": "web_search", "status": "running"},
        )
        assert event.event_type == StreamEventType.TOOL_STATUS

    def test_thinking_event(self):
        """Create a thinking streaming event."""
        event = StreamEvent(
            request_id="req-1",
            event_type=StreamEventType.THINKING,
            data={"content": "internal reasoning"},
        )
        assert event.event_type == StreamEventType.THINKING

    def test_done_event(self):
        """Create a done streaming event."""
        event = StreamEvent(
            request_id="req-1",
            event_type=StreamEventType.DONE,
            data="",
        )
        assert event.event_type == StreamEventType.DONE

    def test_json_roundtrip(self):
        """Serialize to JSON and deserialize back."""
        event = StreamEvent(
            request_id="req-1",
            event_type=StreamEventType.TOKEN,
            data="word",
        )
        restored = StreamEvent.model_validate_json(event.model_dump_json())
        assert restored == event

    def test_extra_field_rejected(self):
        """Reject unknown fields."""
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            StreamEvent(
                request_id="req-1",
                event_type=StreamEventType.TOKEN,
                data="word",
                extra="bad",
            )


class TestDBOperation:
    """Tests for DBOperation model."""

    def test_valid_with_parameters(self):
        """Create a DBOperation with parameters."""
        op = DBOperation(
            request_id="req-1",
            operation="save_message",
            parameters={"conversation_id": "c1", "role": "user", "content": "Hi"},
        )
        assert op.operation == "save_message"

    def test_valid_without_parameters(self):
        """Create a DBOperation without parameters."""
        op = DBOperation(
            request_id="req-1",
            operation="verify_chain",
        )
        assert op.parameters is None

    def test_json_roundtrip(self):
        """Serialize to JSON and deserialize back."""
        op = DBOperation(
            request_id="req-1",
            operation="get_stats",
        )
        restored = DBOperation.model_validate_json(op.model_dump_json())
        assert restored == op

    def test_extra_field_rejected(self):
        """Reject unknown fields."""
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            DBOperation(
                request_id="req-1",
                operation="save_message",
                sql="SELECT 1",
            )


class TestDBResult:
    """Tests for DBResult model."""

    def test_success_result(self):
        """Create a successful DBResult."""
        result = DBResult(
            request_id="req-1",
            status="success",
            data={"message_id": "m1"},
        )
        assert result.status == "success"
        assert result.error_message is None

    def test_error_result(self):
        """Create an error DBResult."""
        result = DBResult(
            request_id="req-1",
            status="error",
            error_message="Unknown operation",
        )
        assert result.status == "error"
        assert result.data is None

    def test_json_roundtrip(self):
        """Serialize to JSON and deserialize back."""
        result = DBResult(
            request_id="req-1",
            status="success",
            data={"count": 42},
        )
        restored = DBResult.model_validate_json(result.model_dump_json())
        assert restored == result
