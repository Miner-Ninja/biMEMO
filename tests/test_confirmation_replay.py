"""Contract tests: typed input never resolves a confirmation (R12, R13)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

import backend.agent.llm_caller as llm_caller_module
import backend.processes.orchestrator.chat_handler as chat_handler_module
from backend.ipc.models import ChatRequest, ChatResponse, TurnOutcomeKind
from backend.processes.orchestrator.chat_handler import (
    _maybe_handle_permission_reply,
)


def _request(message: str) -> ChatRequest:
    return ChatRequest(
        request_id="r1",
        run_id="run-req-1",
        message=message,
        channel="web",
        session_id="s1",
        conversation_id="conv-1",
    )


def _db_client_with_pending() -> MagicMock:
    db_client = MagicMock()
    db_client.list_pending_agent_confirmations = AsyncMock(
        return_value=[
            {
                "confirmation_id": "confirm-1",
                "turn_id": "turn-1",
                "tool_name": "execute_command",
                "arguments": {"command": "ls"},
                "channel": "web",
            }
        ]
    )
    db_client.get_agent_turn = AsyncMock(
        return_value={
            "turn_id": "turn-1",
            "conversation_id": "conv-1",
            "session_id": "s1",
            "channel": "web",
            "status": "paused",
            "terminal_outcome": "paused",
            "outcome_payload": {},
            "confirmations": [
                {
                    "confirmation_id": "confirm-1",
                    "tool_name": "execute_command",
                    "parameters": {"command": "ls"},
                    "status": "pending",
                }
            ],
            "invocations": [],
            "pending_input": None,
        }
    )
    return db_client


class TestTypedTextResolvesNothing:
    """R12 — no vocabulary, no digits, no commands, no LLM."""

    @pytest.mark.parametrize(
        "message",
        ["да", "yes", "1", "/approve", "нет", "no", "/deny", "хм, а это надо?"],
    )
    @pytest.mark.asyncio
    async def test_any_message_replays_the_paused_card(self, message):
        """S12.1/S12.2: every typed message returns the unchanged pause."""
        db_client = _db_client_with_pending()
        orch = MagicMock()
        orch.resume_structured_confirmation = AsyncMock()
        llm_call = AsyncMock()

        response = await _maybe_handle_permission_reply(
            request=_request(message),
            orch=orch,
            db_client=db_client,
            llm_call=llm_call,
            run_event_context=None,
        )

        assert isinstance(response, ChatResponse)
        assert response.outcome_kind is TurnOutcomeKind.PAUSED
        assert response.pending_confirmations
        assert response.pending_confirmations[0]["action_id"] == "confirm-1"
        orch.resume_structured_confirmation.assert_not_awaited()
        llm_call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_pending_means_no_interception(self):
        """Ordinary chat is untouched when nothing is pending."""
        db_client = MagicMock()
        db_client.list_pending_agent_confirmations = AsyncMock(return_value=[])
        response = await _maybe_handle_permission_reply(
            request=_request("привет"),
            orch=MagicMock(),
            db_client=db_client,
            llm_call=AsyncMock(),
            run_event_context=None,
        )
        assert response is None


class TestClassifierRemoved:
    """R13/S13.1 — the LLM permission classifier does not exist."""

    def test_classifier_symbols_absent(self):
        for symbol in (
            "classify_permission_reply",
            "_parse_permission_reply",
            "PERMISSION_REPLY_PROMPT",
            "PERMISSION_REPLY_SCHEMA",
        ):
            assert not hasattr(llm_caller_module, symbol), symbol
            assert not hasattr(llm_caller_module.LLMCaller, symbol), symbol

    def test_vocabulary_parser_absent(self):
        assert not hasattr(chat_handler_module, "_parse_confirmation_command")
