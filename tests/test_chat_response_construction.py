"""Tests for structured assistant-response construction.

Covers the message-item and chat-response builders: structured
content parsing, legacy answer normalization, the visible-answer
extraction, pending-confirmation serialization, stream-enabled
propagation, and the history-reload attachment of pending
confirmations to assistant messages.
"""

import asyncio
from types import SimpleNamespace

import pytest

from backend.agent.agent_loop_models import AgentResponse
from backend.ipc.models import (
    ChatResponse as IPCChatResponse,
)
from backend.ipc.models import (
    StructuredAssistantContent as IPCStructuredAssistantContent,
)
from backend.llm.structured_response import normalize_legacy_response_text
from backend.models.responses import StructuredAssistantContent
from backend.processes.fastapi_app.routes.chat_helpers import (
    build_conversation_message_item,
    build_http_chat_response,
    list_conversation_message_page,
)
from backend.processes.orchestrator.chat_handler import (
    _build_chat_response,
    _pending_confirmations_from_orchestrator,
)
from backend.trust.confirmation import PendingAction


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
        suggestions=["Next step"],
    )


def test_build_chat_response_uses_structured_answer_as_visible_text() -> None:
    """Return the normalized answer text when structured content is available."""
    chat_response = _build_chat_response(
        request_id="request-1",
        response=AgentResponse(
            structured_content=normalize_legacy_response_text(
                "\n".join(
                    [
                        "Main answer.",
                        "",
                        "```sql",
                        "SELECT 1",
                        "```",
                    ]
                )
            ),
            loop_result=object(),
        ),
        transcription=None,
    )

    assert chat_response.structured_content is not None
    assert chat_response.structured_content.answer == "Main answer."
    assert chat_response.structured_content.suggestions == []


def test_pending_confirmations_from_orchestrator_serializes_red_calls() -> None:
    """Expose RED-zone pending actions in the IPC chat response payload."""
    orch = SimpleNamespace(
        pending_red_calls=[
            PendingAction(
                action_id="action-1",
                tool_name="http_fetch",
                parameters={"url": "https://example.com"},
                channel="web",
                created_at=0.0,
            )
        ]
    )

    assert _pending_confirmations_from_orchestrator(orch) == [
        {
            "action_id": "action-1",
            "tool_name": "http_fetch",
            "parameters": {"url": "https://example.com"},
            "grant_capable": True,
            "url_host": "example.com",
        }
    ]


@pytest.mark.parametrize("stream_enabled_value", ["true", "false"])
def test_build_chat_response_propagates_stream_enabled_setting(
    monkeypatch: pytest.MonkeyPatch,
    stream_enabled_value: str,
) -> None:
    """Mirror BIMEMO_LLM_RESPONSE_STREAM onto the IPC ChatResponse."""
    from backend.config import reset_settings

    monkeypatch.setenv("BIMEMO_LLM_RESPONSE_STREAM", stream_enabled_value)
    reset_settings()
    try:
        chat_response = _build_chat_response(
            request_id="request-stream",
            response=AgentResponse(
                structured_content=normalize_legacy_response_text("Hi."),
                loop_result=object(),
            ),
            transcription=None,
        )
    finally:
        reset_settings()

    assert chat_response.stream_enabled is (stream_enabled_value == "true")


def test_build_http_chat_response_creates_structured_answer_from_text() -> None:
    """Expose one structured answer block even for plain IPC chat responses."""
    http_response = build_http_chat_response(
        conversation_id="conversation-1",
        ipc_response=IPCChatResponse(
            request_id="request-1",
            structured_content=IPCStructuredAssistantContent(answer="Plain answer"),
            input_filtered=False,
            output_filtered=False,
            stream_enabled=True,
        ),
    )

    assert http_response.structured_content == StructuredAssistantContent(
        answer="Plain answer",
        thinking=None,
        suggestions=[],
    )
    assert http_response.stream_enabled is True


class _FakePendingAdminClient:
    """Admin client double serving the core-turn confirmation sources."""

    def __init__(
        self,
        messages: list[dict],
        runs_by_conversation: dict[str, list[dict]] | None = None,
        confirmations_by_run: dict[str, list[dict]] | None = None,
        events_by_run: dict[str, list[dict]] | None = None,
    ):
        self._messages = messages
        self._runs = runs_by_conversation or {}
        self._confirmations = confirmations_by_run or {}
        self._events = events_by_run or {}

    async def list_messages(
        self, limit: int = 50, offset: int = 0, conversation_id: str | None = None
    ) -> list[dict]:
        return self._messages

    async def list_message_page(
        self,
        *,
        conversation_id: str | None = None,
        limit: int = 200,
        cursor: str | None = None,
    ) -> dict:
        del conversation_id, cursor
        ordered = sorted(
            self._messages,
            key=lambda message: str(message.get("created_at", "")),
            reverse=True,
        )
        return {
            "messages": ordered[:limit],
            "next_cursor": None,
            "has_more": False,
        }

    async def list_agent_runs(
        self,
        limit: int = 50,
        offset: int = 0,
        conversation_id: str | None = None,
    ) -> list[dict]:
        return list(self._runs.get(conversation_id or "", []))[:limit]

    async def list_agent_confirmations(
        self, turn_id: str, limit: int = 100
    ) -> list[dict]:
        return list(self._confirmations.get(turn_id, []))[:limit]

    async def list_agent_run_events(self, run_id: str, limit: int = 500) -> list[dict]:
        return list(self._events.get(run_id, []))[:limit]


def test_waiting_confirmation_attaches_to_last_assistant_message() -> None:
    """A persisted un-decided RED card comes back on its assistant message."""
    messages = [
        {
            "id": "user-1",
            "role": "user",
            "content": "run git status",
            "conversation_id": "conv-1",
            "created_at": "2026-08-12T10:00:00",
            "meta": None,
        },
        {
            "id": "assistant-1",
            "role": "assistant",
            "content": "Working on it.",
            "conversation_id": "conv-1",
            "created_at": "2026-08-12T10:00:01",
            "meta": None,
        },
    ]
    admin_client = _FakePendingAdminClient(
        messages=messages,
        runs_by_conversation={"conv-1": [{"id": "run-1"}]},
        confirmations_by_run={
            "run-1": [{"confirmation_id": "action-1", "status": "pending"}]
        },
        events_by_run={
            "run-1": [
                {
                    "event_type": "run_paused",
                    "payload": {
                        "pending_confirmations": [
                            {
                                "action_id": "action-1",
                                "tool_name": "http_fetch",
                                "parameters": {"url": "https://example.com"},
                                "grant_capable": True,
                                "url_host": "example.com",
                            }
                        ]
                    },
                }
            ]
        },
    )

    page = asyncio.run(
        list_conversation_message_page(
            admin_client=admin_client,
            conversation_id="conv-1",
            limit=50,
            cursor=None,
        )
    )
    items = page.messages

    assistant = next(item for item in items if item.id == "assistant-1")
    assert assistant.pending_confirmations
    card = assistant.pending_confirmations[0]
    assert card.action_id == "action-1"
    assert card.tool_name == "http_fetch"
    assert card.grant_capable is True
    assert card.url_host == "example.com"
    user = next(item for item in items if item.id == "user-1")
    assert not user.pending_confirmations


def test_waiting_confirmation_from_other_conversation_is_not_attached() -> None:
    """A decided confirmation never resurfaces on history reload."""
    messages = [
        {
            "id": "assistant-1",
            "role": "assistant",
            "content": "Done.",
            "conversation_id": "conv-1",
            "created_at": "2026-08-12T10:00:01",
            "meta": None,
        },
    ]
    admin_client = _FakePendingAdminClient(
        messages=messages,
        runs_by_conversation={"conv-1": [{"id": "run-1"}]},
        confirmations_by_run={
            "run-1": [{"confirmation_id": "action-1", "status": "approved"}]
        },
        events_by_run={
            "run-1": [
                {
                    "event_type": "run_paused",
                    "payload": {
                        "pending_confirmations": [
                            {
                                "action_id": "action-1",
                                "tool_name": "http_fetch",
                                "parameters": {},
                            }
                        ]
                    },
                }
            ]
        },
    )

    page = asyncio.run(
        list_conversation_message_page(
            admin_client=admin_client,
            conversation_id="conv-1",
            limit=50,
            cursor=None,
        )
    )
    items = page.messages

    assistant = next(item for item in items if item.id == "assistant-1")
    assert not assistant.pending_confirmations
