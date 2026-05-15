"""LLM call factory shared by all provider clients.

Wraps a client's .chat() method with timeout protection, optional SSE
queue forwarding, and per-call overrides via ``LLMCallOptions``.

The ``LLMCallOptions`` value type is the single documented per-call
override entry point. Legacy keyword arguments (``tools_override``,
``tool_choice``, ``sse_queue_override``) remain accepted for backward
compatibility; when both are passed ``options`` wins and a warning is
logged.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
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
        sse_queue: asyncio.Queue | None = None,
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
    sse_queue_override: asyncio.Queue | None,
    tools_override: list[dict] | None,
    tool_choice: object | None,
    default_sse_queue: asyncio.Queue | None,
    default_tools: list[dict] | None,
) -> LLMCallOptions:
    """Resolve per-call options, honouring legacy kwargs when needed.

    When ``options`` is provided it wins over legacy kwargs and a warning
    is logged if both are set. When ``options`` is ``None`` a synthesised
    value is built from the legacy kwargs so downstream code sees a
    single consistent type.
    """
    legacy_set = (
        sse_queue_override is not None
        or tools_override is not None
        or tool_choice is not None
    )
    if options is not None:
        if legacy_set:
            logger.warning(
                "LLM call received both options=... and legacy kwargs; "
                "options wins and legacy kwargs are ignored",
            )
        return options
    return LLMCallOptions(
        tools=_selected_value(tools_override, default_tools),
        tool_choice=tool_choice,
        sse_queue=_selected_value(sse_queue_override, default_sse_queue),
    )


async def _call_with_timeout(
    client: LLMClient,
    *,
    messages: list[dict],
    options: LLMCallOptions,
    default_sse_queue: asyncio.Queue | None,
    default_tools: list[dict] | None,
) -> LLMResponse:
    """Call the client once with the shared LLM timeout."""
    effective_sse_queue = _selected_value(options.sse_queue, default_sse_queue)
    effective_tools = _selected_value(options.tools, default_tools)
    chat_options = LLMCallOptions(
        response_format=options.response_format,
        response_schema=options.response_schema,
        tools=effective_tools,
        tool_choice=options.tool_choice,
        sse_queue=effective_sse_queue,
    )
    return await asyncio.wait_for(
        client.chat(messages, options=chat_options),
        timeout=LLM_CALL_TIMEOUT,
    )


def make_llm_call(
    client: LLMClient,
    sse_queue: asyncio.Queue | None = None,
    tools: list[dict] | None = None,
) -> Callable[[list[dict], asyncio.Queue | None], Awaitable[LLMResponse]]:
    """Create an async LLM call function for the orchestrator.

    Args:
        client: LLM client with a .chat(messages, options=) method.
        sse_queue: Optional caller-scoped default SSE queue.
        tools: Optional caller-scoped default OpenAI-format tool schemas.

    Returns:
        Async callable accepting messages and optional LLMCallOptions.
    """

    async def llm_call(
        messages: list[dict],
        sse_queue_override: asyncio.Queue | None = None,
        *,
        options: LLMCallOptions | None = None,
        tools_override: list[dict] | None = None,
        tool_choice: object | None = None,
    ) -> LLMResponse:
        """Call the LLM client with timeout protection.

        Args:
            messages: Conversation messages.
            sse_queue_override: Legacy per-call SSE queue override.
            options: Per-call overrides bundle. When provided this wins
                over any legacy kwargs (with a warning log).
            tools_override: Legacy per-call tool schema override.
            tool_choice: Legacy forced tool selection override.

        Returns:
            LLMResponse with text and/or tool calls.
        """
        effective = _resolve_options(
            options=options,
            sse_queue_override=sse_queue_override,
            tools_override=tools_override,
            tool_choice=tool_choice,
            default_sse_queue=sse_queue,
            default_tools=tools,
        )
        return await _call_with_timeout(
            client,
            messages=messages,
            options=effective,
            default_sse_queue=sse_queue,
            default_tools=tools,
        )

    return llm_call
