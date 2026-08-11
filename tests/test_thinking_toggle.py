"""Tests for the per-conversation thinking on/off switch (chat-thinking-toggle).

R2: with the toggle off, the provider request carries the native
reasoning-off parameter; hosted providers get no extra key. R6/S: the
holder resets, and reading a missing setting yields the default (on).
"""

from unittest.mock import AsyncMock

import pytest

from backend.agent.conversation_settings import read_thinking_enabled
from backend.llm.call_factory import (
    current_thinking_enabled,
    set_thinking_enabled,
)
from backend.llm.capabilities import LlmResponseFormat
from backend.llm.openai_compatible.client import (
    CUSTOM_API_PREFIX,
    DEFAULT_OPENAI_API_PREFIX,
    _chat_payload,
)
from backend.processes.orchestrator.client import DBProxyClient


@pytest.fixture(autouse=True)
def _reset_holder():
    """Never leak the per-turn switch between tests."""
    yield
    set_thinking_enabled(None)


class TestHolder:
    """The per-turn switch stores and clears its value."""

    def test_set_and_clear(self) -> None:
        """S — set False is readable; None clears back to default."""
        set_thinking_enabled(False)
        assert current_thinking_enabled() is False
        set_thinking_enabled(None)
        assert current_thinking_enabled() is None


class TestChatPayloadInjection:
    """R2 — the OpenAI-compatible payload carries the vLLM switch."""

    def _payload(self, *, thinking_enabled, api_prefix):
        return _chat_payload(
            model="m",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            llm_response_format=LlmResponseFormat.TEXT,
            stream=False,
            thinking_enabled=thinking_enabled,
            api_prefix=api_prefix,
        )

    def test_off_on_custom_prefix_injects_kwargs(self) -> None:
        """S1.1 — custom (vLLM) + off => chat_template_kwargs sent."""
        payload = self._payload(
            thinking_enabled=False, api_prefix=CUSTOM_API_PREFIX
        )
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}

    def test_off_on_hosted_prefix_sends_nothing(self) -> None:
        """R6 — hosted providers get the request unchanged."""
        payload = self._payload(
            thinking_enabled=False, api_prefix=DEFAULT_OPENAI_API_PREFIX
        )
        assert "chat_template_kwargs" not in payload

    def test_default_none_sends_nothing(self) -> None:
        """S1.4 — no switch set => today's behaviour."""
        payload = self._payload(
            thinking_enabled=None, api_prefix=CUSTOM_API_PREFIX
        )
        assert "chat_template_kwargs" not in payload


class TestReadThinkingEnabled:
    """R1 — the per-conversation setting reads with a safe default."""

    @pytest.mark.asyncio
    async def test_missing_key_defaults_on(self) -> None:
        """An unset conversation keeps reasoning on."""
        client = DBProxyClient(ipc_client=AsyncMock())
        client.send_operation = AsyncMock(  # type: ignore[method-assign]
            return_value={"settings": {}}
        )
        assert await read_thinking_enabled(client, "conv-1") is True

    @pytest.mark.asyncio
    async def test_stored_zero_reads_off(self) -> None:
        """A stored "0" turns reasoning off."""
        client = DBProxyClient(ipc_client=AsyncMock())
        client.send_operation = AsyncMock(  # type: ignore[method-assign]
            return_value={"settings": {"thinking_enabled": "0"}}
        )
        assert await read_thinking_enabled(client, "conv-1") is False

    @pytest.mark.asyncio
    async def test_read_error_falls_back_to_default(self) -> None:
        """A settings hiccup cannot break a chat turn."""
        client = DBProxyClient(ipc_client=AsyncMock())
        client.send_operation = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("boom")
        )
        assert await read_thinking_enabled(client, "conv-1") is True
