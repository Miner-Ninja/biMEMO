"""Tests for structured assistant-response helpers."""

from backend.agent.agent_loop_models import AgentResponse
from backend.ipc.models import ChatResponse as IPCChatResponse
from backend.llm.structured_response import normalize_legacy_response_text
from backend.models.responses import StructuredAssistantContent
from backend.processes.fastapi_app.routes.chat_helpers import (
    build_conversation_message_item,
    build_http_chat_response,
)
from backend.processes.orchestrator.chat_handler import _build_chat_response


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
                '"thinking": "Internal plan", "sql": "SELECT 1", '
                '"suggestions": ["Next step"]}}'
            ),
        }
    )

    assert message_item.content == "Visible answer"
    assert message_item.structured_content == StructuredAssistantContent(
        answer="Visible answer",
        thinking="Internal plan",
        sql="SELECT 1",
        suggestions=["Next step"],
    )


def test_build_conversation_message_item_uses_content_as_saved_answer() -> None:
    """Read the visible answer from content when new metadata omits it."""
    message_item = build_conversation_message_item(
        {
            "id": "message-1b",
            "role": "assistant",
            "content": "Visible answer",
            "conversation_id": "conversation-1",
            "created_at": "2026-04-12T10:00:00",
            "meta": (
                '{"structured_content": {"thinking": "Internal plan", '
                '"sql": "SELECT 1", "suggestions": ["Next step"]}}'
            ),
        }
    )

    assert message_item.content == "Visible answer"
    assert message_item.structured_content == StructuredAssistantContent(
        answer="Visible answer",
        thinking="Internal plan",
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


def test_build_chat_response_uses_structured_answer_as_visible_text() -> None:
    """Return the normalized answer text when structured content is available."""
    chat_response = _build_chat_response(
        request_id="request-1",
        response=AgentResponse(
            text="\n".join(
                [
                    "Main answer.",
                    "",
                    "```sql",
                    "SELECT 1",
                    "```",
                ]
            ),
            loop_result=object(),
        ),
        transcription=None,
    )

    assert chat_response.text == "Main answer."
    assert chat_response.structured_content is not None
    assert chat_response.structured_content.answer == "Main answer."
    assert chat_response.structured_content.sql == "SELECT 1"
    assert chat_response.structured_content.suggestions == []


def test_build_http_chat_response_creates_structured_answer_from_text() -> None:
    """Expose one structured answer block even for plain IPC chat responses."""
    http_response = build_http_chat_response(
        conversation_id="conversation-1",
        ipc_response=IPCChatResponse(
            request_id="request-1",
            text="Plain answer",
            input_filtered=False,
            output_filtered=False,
        ),
    )

    assert http_response.text == "Plain answer"
    assert http_response.structured_content == StructuredAssistantContent(
        answer="Plain answer",
        thinking=None,
        sql=None,
        suggestions=[],
    )
