"""Tests for the LLM call factory and per-call option resolution.

Covers make_llm_call, _resolve_options, and _call_with_timeout, including
backward-compatible legacy kwargs, LLMCallOptions precedence, and
propagation of overrides into the underlying client.chat(options=...)
call.
"""

import asyncio
import logging

import pytest

from backend.llm.call_factory import (
    LLMResponse,
    _call_with_timeout,
    _resolve_options,
    make_llm_call,
)
from backend.llm.call_options import LLMCallOptions
from backend.llm.capabilities import LlmResponseFormat


class _RecordingClient:
    """Test double that records the options passed into chat()."""

    def __init__(self, response: LLMResponse | None = None):
        """Initialize with an optional canned response."""
        self.response = response or LLMResponse(text="ok")
        self.received_messages: list[dict] | None = None
        self.received_options: LLMCallOptions | None = None

    async def chat(
        self,
        messages: list[dict],
        *,
        options: LLMCallOptions | None = None,
    ) -> LLMResponse:
        """Record call arguments and return the canned response."""
        self.received_messages = messages
        self.received_options = options
        return self.response


class TestResolveOptions:
    """Tests for the _resolve_options helper."""

    def test_none_options_with_no_legacy_kwargs_returns_defaults(self):
        """Verify default resolution uses the caller-scoped defaults."""
        resolved = _resolve_options(
            options=None,
            sse_queue_override=None,
            tools_override=None,
            tool_choice=None,
            default_sse_queue=None,
            default_tools=None,
        )
        assert resolved.tools is None
        assert resolved.tool_choice is None
        assert resolved.sse_queue is None
        assert resolved.response_format is None

    def test_none_options_picks_legacy_tools_override(self):
        """Verify legacy tools_override flows into options.tools."""
        tools = [{"type": "function"}]
        resolved = _resolve_options(
            options=None,
            sse_queue_override=None,
            tools_override=tools,
            tool_choice=None,
            default_sse_queue=None,
            default_tools=None,
        )
        assert resolved.tools == tools

    def test_none_options_falls_back_to_default_tools_when_override_missing(self):
        """Verify the caller-scoped default tools win when override is None."""
        default_tools = [{"type": "function", "default": True}]
        resolved = _resolve_options(
            options=None,
            sse_queue_override=None,
            tools_override=None,
            tool_choice=None,
            default_sse_queue=None,
            default_tools=default_tools,
        )
        assert resolved.tools == default_tools

    def test_none_options_picks_legacy_tool_choice(self):
        """Verify legacy tool_choice flows into options.tool_choice."""
        resolved = _resolve_options(
            options=None,
            sse_queue_override=None,
            tools_override=None,
            tool_choice="none",
            default_sse_queue=None,
            default_tools=None,
        )
        assert resolved.tool_choice == "none"

    def test_explicit_options_wins_over_legacy_kwargs(self, caplog):
        """Verify options beats legacy kwargs and a warning is logged."""
        options = LLMCallOptions(tools=[{"type": "function", "winner": True}])
        with caplog.at_level(logging.WARNING):
            resolved = _resolve_options(
                options=options,
                sse_queue_override=None,
                tools_override=[{"type": "function", "loser": True}],
                tool_choice=None,
                default_sse_queue=None,
                default_tools=None,
            )
        assert resolved is options
        assert any("options wins" in record.getMessage() for record in caplog.records)

    def test_options_without_legacy_kwargs_emits_no_warning(self, caplog):
        """Verify no warning is logged when only options is passed."""
        options = LLMCallOptions(response_format=LlmResponseFormat.TEXT)
        with caplog.at_level(logging.WARNING):
            resolved = _resolve_options(
                options=options,
                sse_queue_override=None,
                tools_override=None,
                tool_choice=None,
                default_sse_queue=None,
                default_tools=None,
            )
        assert resolved is options
        assert caplog.records == []

    def test_legacy_sse_queue_override_flows_into_options(self):
        """Verify legacy sse_queue_override lands in the synthesised options."""
        queue: asyncio.Queue = asyncio.Queue()
        resolved = _resolve_options(
            options=None,
            sse_queue_override=queue,
            tools_override=None,
            tool_choice=None,
            default_sse_queue=None,
            default_tools=None,
        )
        assert resolved.sse_queue is queue


class TestCallWithTimeout:
    """Tests for the _call_with_timeout helper."""

    @pytest.mark.asyncio
    async def test_passes_messages_and_options_to_client(self):
        """Verify client.chat receives the messages and options bundle."""
        client = _RecordingClient()
        options = LLMCallOptions(response_format=LlmResponseFormat.TEXT)
        messages = [{"role": "user", "content": "hi"}]
        await _call_with_timeout(
            client,  # type: ignore[arg-type]
            messages=messages,
            options=options,
            default_sse_queue=None,
            default_tools=None,
        )
        assert client.received_messages is messages
        assert client.received_options is not None
        assert client.received_options.response_format is LlmResponseFormat.TEXT

    @pytest.mark.asyncio
    async def test_applies_default_tools_when_options_tools_is_none(self):
        """Verify default tools backfill when options.tools is not set."""
        client = _RecordingClient()
        default_tools = [{"type": "function", "default": True}]
        await _call_with_timeout(
            client,  # type: ignore[arg-type]
            messages=[{"role": "user", "content": "hi"}],
            options=LLMCallOptions(),
            default_sse_queue=None,
            default_tools=default_tools,
        )
        assert client.received_options is not None
        assert client.received_options.tools == default_tools

    @pytest.mark.asyncio
    async def test_options_tools_empty_list_beats_default_tools(self):
        """Verify options.tools=[] wins over a caller-scoped default."""
        client = _RecordingClient()
        default_tools = [{"type": "function", "default": True}]
        await _call_with_timeout(
            client,  # type: ignore[arg-type]
            messages=[{"role": "user", "content": "hi"}],
            options=LLMCallOptions(tools=[]),
            default_sse_queue=None,
            default_tools=default_tools,
        )
        assert client.received_options is not None
        assert client.received_options.tools == []

    @pytest.mark.asyncio
    async def test_applies_default_sse_queue_when_options_queue_is_none(self):
        """Verify default sse_queue backfill when options.sse_queue is None."""
        client = _RecordingClient()
        default_queue: asyncio.Queue = asyncio.Queue()
        await _call_with_timeout(
            client,  # type: ignore[arg-type]
            messages=[{"role": "user", "content": "hi"}],
            options=LLMCallOptions(),
            default_sse_queue=default_queue,
            default_tools=None,
        )
        assert client.received_options is not None
        assert client.received_options.sse_queue is default_queue


class TestMakeLLMCall:
    """Tests for the make_llm_call callable factory."""

    @pytest.mark.asyncio
    async def test_returned_callable_invokes_client(self):
        """Verify calling the returned function calls client.chat()."""
        client = _RecordingClient(response=LLMResponse(text="hello"))
        llm_call = make_llm_call(client)  # type: ignore[arg-type]
        result = await llm_call([{"role": "user", "content": "hi"}])
        assert result.text == "hello"

    @pytest.mark.asyncio
    async def test_options_propagates_through_factory(self):
        """Verify explicit LLMCallOptions reaches the underlying client."""
        client = _RecordingClient()
        llm_call = make_llm_call(client)  # type: ignore[arg-type]
        schema = {"type": "object", "properties": {}, "required": []}
        await llm_call(
            [{"role": "user", "content": "hi"}],
            options=LLMCallOptions(
                response_format=LlmResponseFormat.JSON_SCHEMA,
                response_schema=schema,
            ),
        )
        assert client.received_options is not None
        assert client.received_options.response_format is LlmResponseFormat.JSON_SCHEMA
        assert client.received_options.response_schema is schema

    @pytest.mark.asyncio
    async def test_legacy_tools_override_still_works(self):
        """Verify pre-existing tools_override kwarg continues to function."""
        client = _RecordingClient()
        llm_call = make_llm_call(client)  # type: ignore[arg-type]
        await llm_call(
            [{"role": "user", "content": "hi"}],
            tools_override=[],
        )
        assert client.received_options is not None
        assert client.received_options.tools == []

    @pytest.mark.asyncio
    async def test_options_wins_over_legacy_kwargs_in_factory(self, caplog):
        """Verify explicit options beats legacy kwargs through make_llm_call."""
        client = _RecordingClient()
        llm_call = make_llm_call(client)  # type: ignore[arg-type]
        options_tools: list[dict] = [
            {"type": "function", "function": {"name": "winner"}},
        ]
        legacy_tools: list[dict] = [
            {"type": "function", "function": {"name": "loser"}},
        ]
        with caplog.at_level(logging.WARNING):
            await llm_call(
                [{"role": "user", "content": "hi"}],
                options=LLMCallOptions(tools=options_tools),
                tools_override=legacy_tools,
            )
        assert client.received_options is not None
        assert client.received_options.tools == options_tools
        assert any("options wins" in record.getMessage() for record in caplog.records)

    @pytest.mark.asyncio
    async def test_caller_scoped_defaults_survive_through_factory(self):
        """Verify default tools passed to make_llm_call reach the client."""
        client = _RecordingClient()
        default_tools = [{"type": "function", "function": {"name": "default"}}]
        llm_call = make_llm_call(client, tools=default_tools)  # type: ignore[arg-type]
        await llm_call([{"role": "user", "content": "hi"}])
        assert client.received_options is not None
        assert client.received_options.tools == default_tools
