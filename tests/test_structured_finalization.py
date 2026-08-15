"""Contract tests for buffered final-only structured synthesis."""

import pytest
from backend.agent.structured_finalization import (
    StructuredFinalizationError,
    StructuredFinalizer,
)
from backend.llm.call_factory import LLMResponse
from backend.llm.call_options import EmptyRetry, EventSink, ResponseStream
from backend.llm.capabilities import LlmResponseFormat


async def test_malformed_attempt_retries_with_identical_buffered_options():
    """Only invalid JSON retries; both calls stay buffered and tool-disabled."""
    calls = []

    async def llm_call(messages, *, options, phase_override):
        calls.append((messages, options, phase_override))
        text = "not-json" if len(calls) == 1 else '{"answer":"done","suggestions":[]}'
        return LLMResponse(text=text)

    result = await StructuredFinalizer(llm_call=llm_call).finalize(
        [{"role": "user", "content": "answer"}]
    )
    assert result.content.answer == "done"
    assert result.attempts == 2
    assert calls[0][0] == calls[1][0]
    options = calls[0][1]
    assert options.response_format is LlmResponseFormat.JSON_SCHEMA
    assert options.tools == []
    assert options.event_sink is EventSink.DISABLED
    assert options.response_stream is ResponseStream.DISABLED
    assert options.empty_retry is EmptyRetry.DISABLED
    assert options.thinking_enabled is False


async def test_tool_call_in_synthesis_fails_without_retry():
    """A wrong response channel is a terminal provider violation."""
    from backend.llm.call_factory import LLMToolCall

    attempts = 0

    async def llm_call(_messages, **_kwargs):
        nonlocal attempts
        attempts += 1
        return LLMResponse(
            text="",
            tool_calls=[LLMToolCall(id="call-1", name="web_search", arguments={})],
        )

    with pytest.raises(StructuredFinalizationError, match="tools disabled"):
        await StructuredFinalizer(llm_call=llm_call).finalize([])
    assert attempts == 1


async def test_required_evidence_overflow_prevents_provider_call():
    """Mandatory evidence is never silently truncated to fit synthesis."""
    called = False

    async def llm_call(_messages, **_kwargs):
        nonlocal called
        called = True
        return LLMResponse(text='{"answer":"wrong","suggestions":[]}')

    messages = [
        {
            "role": "assistant",
            "tool_calls": [{"id": "call-1", "type": "function"}],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "x" * 1000},
    ]
    with pytest.raises(StructuredFinalizationError, match="does not fit"):
        await StructuredFinalizer(
            llm_call=llm_call,
            max_context_tokens=4097,
        ).finalize(messages)
    assert called is False
