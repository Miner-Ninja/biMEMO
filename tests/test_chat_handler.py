"""Tests for structured assistant-response helpers."""

from backend.llm.structured_response import normalize_legacy_response_text
from backend.models.responses import StructuredAssistantContent
from backend.processes.fastapi_app.routes.chat_helpers import (
    build_conversation_message_item,
)


def test_build_structured_content_extracts_sql_and_suggestions() -> None:
    """Extract SQL and suggestion blocks from one assistant answer."""
    structured = normalize_legacy_response_text(
        "\n".join(
            [
                "Main answer.",
                "",
                "```sql",
                "SELECT 1",
                "```",
                "",
                "```suggestions",
                "Next step",
                "Another step",
                "```",
            ]
        )
    )
    assert structured.answer == "Main answer."
    assert structured.sql == "SELECT 1"
    assert structured.suggestions == ["Next step", "Another step"]


def test_build_structured_content_drops_language_mismatched_suggestions() -> None:
    """Drop suggestions when their script does not match the answer."""
    structured = normalize_legacy_response_text(
        "\n".join(
            [
                "Русский ответ.",
                "",
                "```suggestions",
                "Open the report",
                "Check the dashboard",
                "```",
            ]
        )
    )
    assert structured.answer == "Русский ответ."
    assert structured.suggestions == []


def test_build_conversation_message_item_uses_saved_structured_content() -> None:
    """Prefer saved structured content when assistant metadata already has it."""
    message_item = build_conversation_message_item(
        {
            "id": "message-1",
            "role": "assistant",
            "content": "Raw answer\n```sql\nSELECT 1\n```",
            "conversation_id": "conversation-1",
            "created_at": "2026-04-12T10:00:00",
            "meta": (
                '{"structured_content": {"answer": "Visible answer", '
                '"sql": "SELECT 1", "suggestions": ["Next step"]}}'
            ),
        }
    )

    assert message_item.content == "Visible answer"
    assert message_item.structured_content == StructuredAssistantContent(
        answer="Visible answer",
        sql="SELECT 1",
        suggestions=["Next step"],
    )


def test_build_conversation_message_item_normalizes_legacy_assistant_text() -> None:
    """Normalize old assistant messages when structured content is missing."""
    message_item = build_conversation_message_item(
        {
            "id": "message-2",
            "role": "assistant",
            "content": "\n".join(
                [
                    "Main answer.",
                    "",
                    "```sql",
                    "SELECT 1",
                    "```",
                    "",
                    "```suggestions",
                    "Next step",
                    "```",
                ]
            ),
            "conversation_id": "conversation-1",
            "created_at": "2026-04-12T10:00:00",
            "meta": None,
        }
    )

    assert message_item.content == "Main answer."
    assert message_item.structured_content == StructuredAssistantContent(
        answer="Main answer.",
        sql="SELECT 1",
        suggestions=["Next step"],
    )
