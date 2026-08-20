"""LLM call factory shared by all provider clients.

Wraps a client's .chat() method with timeout protection, optional SSE
queue forwarding, and per-call overrides via ``LLMCallOptions``.

The ``LLMCallOptions`` value type is the single documented per-call
override entry point. Legacy keyword arguments (``tools_override``,
``tool_choice``, ``stream_queue_override``) remain accepted for backward
compatibility; when both are passed ``options`` wins and a warning is
logged.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Protocol

from backend.llm.call_options import LLMCallOptions
from backend.llm.capabilities import LlmResponseFormat


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from backend.llm.structured_response import StructuredAssistantContent
    from backend.llm.usage import LLMCallUsage


logger = logging.getLogger(__name__)

LLM_CALL_TIMEOUT_SECONDS = 120.0
LLM_CALL_TIMEOUT = LLM_CALL_TIMEOUT_SECONDS

#: User-facing warning surfaced by provider clients when the model returns
#: an empty response after the empty-retry.
EMPTY_LLM_RESPONSE_WARNING = (
    "LLM returned an empty response. Check your connection to LLM and try again."
)

# Per-turn reasoning switch. Set by the chat handler before a turn and
# cleared in its finally block; provider clients read it when building
# the request payload. A module-level holder is safe while the
# orchestrator serves one chat request at a time (the IPC server rejects
# a parallel one); if chats ever become concurrent, move this flag into
# LLMCallOptions instead.
_thinking_enabled: bool | None = None


def set_thinking_enabled(value: bool | None) -> None:
    """Set (or clear with None) the per-turn reasoning switch."""
    global _thinking_enabled
    _thinking_enabled = value


def current_thinking_enabled() -> bool | None:
    """Return the per-turn reasoning switch (None = provider default)."""
    return _thinking_enabled


@dataclass(frozen=True)
class AttemptRecord:
    """Record of one LLM provider attempt, including failures without usage."""

    phase: str
    attempt: int
    response: LLMResponse | None = None
    error: str | None = None
    usage: LLMCallUsage | None = None


@dataclass(frozen=True)
class LLMToolCall:
    """A tool call returned by the LLM via native function calling.

    Attributes:
        id: API-assigned call ID (e.g. "call_abc123").
        name: Function name.
        arguments: Parsed arguments dict.
    """

    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class LLMResponse:
    """Structured response from an LLM client.

    Attributes:
        text: Response text (empty when tool_calls present).
        tool_calls: Tool calls requested by the model.
        raw_message: Full assistant message dict for conversation history.
        llm_response_format: Transport mode used for the model response.
    """

    text: str
    tool_calls: list[LLMToolCall] = field(default_factory=list)
    raw_message: dict = field(default_factory=dict)
    usage: LLMCallUsage | None = None
    llm_response_format: LlmResponseFormat = LlmResponseFormat.TEXT
    structured_content: StructuredAssistantContent | None = None


class LLMClient(Protocol):
    """Protocol for LLM client."""

    async def chat(
        self,
        messages: list[dict],
        *,
        stream_queue: asyncio.Queue | None = None,
        tools: list[dict] | None = None,
        options: LLMCallOptions | None = None,
    ) -> LLMResponse:
        """Send messages and return structured response."""
        ...


def _selected_value[T](override: T | None, default: T | None) -> T | None:
    """Return the override when present, otherwise the default value."""
    if override is not None:
        return override
    return default


def _resolve_options(
    *,
    options: LLMCallOptions | None,
    stream_queue_override: asyncio.Queue | None,
    tools_override: list[dict] | None,
    tool_choice: object | None,
    default_stream_queue: asyncio.Queue | None,
    default_tools: list[dict] | None,
) -> LLMCallOptions:
    """Resolve per-call options, honouring legacy kwargs when needed.

    When ``options`` is provided it wins over legacy kwargs and a warning
    is logged if both are set. When ``options`` is ``None`` a synthesised
    value is built from the legacy kwargs so downstream code sees a
    single consistent type.
    """
    legacy_set = (
        stream_queue_override is not None
        or tools_override is not None
        or tool_choice is not None
    )
    if options is not None:
        if legacy_set:
            logger.warning(
                "LLM call received both options=... and legacy kwargs; "
                "options wins and legacy kwargs are ignored",
            )
        # Merge factory-scoped defaults for fields that are None on options.
        resolved = options
        if options.stream_queue is None and default_stream_queue is not None:
            resolved = replace(resolved, stream_queue=default_stream_queue)
        if options.tools is None and default_tools is not None:
            resolved = replace(resolved, tools=default_tools)
        return resolved
    return LLMCallOptions(
        tools=_selected_value(tools_override, default_tools),
        tool_choice=tool_choice,
        stream_queue=_selected_value(stream_queue_override, default_stream_queue),
    )


async def _call_with_timeout(
    client: LLMClient,
    *,
    messages: list[dict],
    options: LLMCallOptions,
    attempt: int = 1,
    phase: str = "default",
    on_attempt: Callable[[AttemptRecord], None] | None = None,
    default_stream_queue: asyncio.Queue | None = None,
    default_tools: list[dict] | None = None,
) -> LLMResponse:
    """Call the client once with the shared LLM timeout.

    Passes options directly to the client without re-applying caller defaults,
    preserving tri-state semantics (None means provider default). Records
    every attempt via on_attempt, including failures without usage data.

    When options.on_attempt is set, the client's two-stage flow handles
    per-attempt recording internally; the outer callback is suppressed to
    avoid duplicate records.
    """
    if options.stream_queue is None and default_stream_queue is not None:
        options = replace(options, stream_queue=default_stream_queue)
    if options.tools is None and default_tools is not None:
        options = replace(options, tools=default_tools)

    inner_handles_callback = options.on_attempt is not None
    try:
        response = await asyncio.wait_for(
            client.chat(messages, options=options),
            timeout=LLM_CALL_TIMEOUT,
        )
    except Exception as exc:
        if on_attempt is not None and not inner_handles_callback:
            on_attempt(
                AttemptRecord(
                    phase=phase,
                    attempt=attempt,
                    error=str(exc),
                )
            )
        raise
    if on_attempt is not None and not inner_handles_callback:
        on_attempt(
            AttemptRecord(
                phase=phase,
                attempt=attempt,
                response=response,
                usage=response.usage,
            )
        )
    return response


def make_llm_call(
    client: LLMClient,
    stream_queue: asyncio.Queue | None = None,
    tools: list[dict] | None = None,
    on_attempt: Callable[[AttemptRecord], None] | None = None,
    phase: str = "default",
) -> Callable[[list[dict], asyncio.Queue | None], Awaitable[LLMResponse]]:
    """Create an async LLM call function for the orchestrator.

    Args:
        client: LLM client with a .chat(messages, options=) method.
        stream_queue: Optional caller-scoped default SSE queue.
        tools: Optional caller-scoped default OpenAI-format tool schemas.
        on_attempt: Optional callback invoked after every provider attempt,
            including failures without usage data.
        phase: Phase label for attempt records (e.g. "Stage 1", "Stage 2").

    Returns:
        Async callable accepting messages and optional LLMCallOptions.
    """
    attempt_counter: int = 0

    async def llm_call(
        messages: list[dict],
        stream_queue_override: asyncio.Queue | None = None,
        *,
        options: LLMCallOptions | None = None,
        tools_override: list[dict] | None = None,
        tool_choice: object | None = None,
        phase_override: str | None = None,
    ) -> LLMResponse:
        """Call the LLM client with timeout protection.

        Args:
            messages: Conversation messages.
            stream_queue_override: Legacy per-call stream queue override.
            options: Per-call overrides bundle. When provided this wins
                over any legacy kwargs (with a warning log).
            tools_override: Legacy per-call tool schema override.
            tool_choice: Legacy forced tool selection override.
            phase_override: Override the phase label for attempt records.

        Returns:
            LLMResponse with text and/or tool calls.
        """
        nonlocal attempt_counter
        attempt_counter += 1
        effective_phase = phase_override if phase_override is not None else phase
        effective = _resolve_options(
            options=options,
            stream_queue_override=stream_queue_override,
            tools_override=tools_override,
            tool_choice=tool_choice,
            default_stream_queue=stream_queue,
            default_tools=tools,
        )
        # Add the factory callback only after resolving legacy kwargs. Creating
        # an options object first would make options win and silently discard
        # legacy per-call tools_override/tool_choice values.
        if effective.on_attempt is None:
            effective = replace(
                effective,
                on_attempt=on_attempt,
                phase=effective_phase,
            )
        return await _call_with_timeout(
            client,
            messages=messages,
            options=effective,
            attempt=attempt_counter,
            phase=effective_phase,
            on_attempt=on_attempt,
        )

    return llm_call
