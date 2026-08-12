"""Tests for API response Pydantic models.

Covers serialization output structure for all
response models.
"""

from backend.models.responses import (
    ChatResponse,
    ConfirmationItem,
    IntegrationListItem,
    StopResponse,
    StructuredAssistantContent,
    SummaryItem,
)


class TestChatResponse:
    """Tests for ChatResponse serialization."""

    def test_serialization(self):
        """Verify JSON structure matches schema."""
        resp = ChatResponse(
            input_filtered=False,
            output_filtered=False,
            pending_confirmations=[],
            structured_content=StructuredAssistantContent(answer="Hello"),
        )
        data = resp.model_dump()
        assert data == {
            "conversation_id": "",
            "input_filtered": False,
            "output_filtered": False,
            "pending_confirmations": [],
            "used_tools": [],
            "retrieved_memories": None,
            "retrieved_messages": None,
            "transcription": None,
            "stats": None,
            "structured_content": {
                "answer": "Hello",
                "thinking": None,
                "suggestions": [],
            },
            "stream_enabled": False,
        }

    def test_with_confirmations(self):
        """Verify confirmations serialized."""
        item = ConfirmationItem(
            action_id="abc",
            tool_name="http_fetch",
            parameters={"url": "https://example.com"},
        )
        resp = ChatResponse(
            input_filtered=False,
            output_filtered=False,
            pending_confirmations=[item],
            structured_content=StructuredAssistantContent(answer="Pending"),
        )
        data = resp.model_dump()
        assert len(data["pending_confirmations"]) == 1
        assert data["pending_confirmations"][0]["tool_name"] == "http_fetch"

    def test_with_structured_content(self):
        """Verify structured assistant content is serialized."""
        resp = ChatResponse(
            input_filtered=False,
            output_filtered=False,
            pending_confirmations=[],
            structured_content=StructuredAssistantContent(
                answer="Answer",
                suggestions=["Next step"],
            ),
        )
        data = resp.model_dump()
        assert data["structured_content"] == {
            "answer": "Answer",
            "thinking": None,
            "suggestions": ["Next step"],
        }


class TestStopResponse:
    """Tests for StopResponse serialization."""

    def test_stopped_true(self):
        """Verify stopped=true serialized."""
        data = StopResponse(stopped=True).model_dump()
        assert data == {"stopped": True}

    def test_stopped_false(self):
        """Verify stopped=false serialized."""
        data = StopResponse(stopped=False).model_dump()
        assert data == {"stopped": False}


class TestIntegrationListItem:
    """Tests for IntegrationListItem serialization."""

    def test_serialization(self):
        """Verify integration fields serialized."""
        data = IntegrationListItem(name="email-work", type="email").model_dump()
        assert data == {
            "name": "email-work",
            "type": "email",
        }


class TestSummaryItem:
    """Tests for SummaryItem serialization."""

    def test_serialization(self):
        """Verify summary fields serialized."""
        data = SummaryItem(
            main_point="User prefers concise answers",
            trust_level="high",
            scope="long_term",
            based_on_count=5,
            created_at="2026-02-14T10:00:00Z",
        ).model_dump()
        assert data["trust_level"] == "high"
        assert data["scope"] == "long_term"
        assert data["based_on_count"] == 5
