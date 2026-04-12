"""LLM call factory shared by all provider clients.

Wraps a client's .chat() method with timeout protection and
optional SSE queue forwarding.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from backend.llm.capabilities import LlmResponseFormat


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from backend.llm.usage import LLMCallUsage


logger = logging.getLogger(__name__)

LLM_CALL_TIMEOUT_SECONDS = 300.0
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


class LLMClient(Protocol):
    """Protocol for LLM client."""

    async def chat(
        self,
        messages: list[dict],
        *,
        sse_queue: asyncio.Queue | None = None,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        """Send messages and return structured response."""
        ...


def _selected_value[T](override: T | None, default: T | None) -> T | None:
    """Return the override when present, otherwise the default value."""
    if override is not None:
        return override
    return default


def _call_options(
    *,
    sse_queue_override: asyncio.Queue | None,
    default_sse_queue: asyncio.Queue | None,
    tools_override: list[dict] | None,
    default_tools: list[dict] | None,
) -> dict[str, object]:
    """Return the effective per-call queue and tool options."""
    return {
        "sse_queue": _selected_value(sse_queue_override, default_sse_queue),
        "tools": _selected_value(tools_override, default_tools),
    }


async def _call_with_timeout(
    client: LLMClient,
    *,
    messages: list[dict],
    sse_queue: asyncio.Queue | None,
    tools: list[dict] | None,
    tool_choice: object | None = None,
) -> LLMResponse:
    """Call the client once with the shared LLM timeout."""
    kwargs: dict[str, object] = {
        "sse_queue": sse_queue,
        "tools": tools,
    }
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    return await asyncio.wait_for(
        client.chat(messages, **kwargs),
        timeout=LLM_CALL_TIMEOUT,
    )


def make_llm_call(
    client: LLMClient,
    sse_queue: asyncio.Queue | None = None,
    tools: list[dict] | None = None,
) -> Callable[[list[dict], asyncio.Queue | None], Awaitable[LLMResponse]]:
    """Create an async LLM call function for the orchestrator.

    Args:
        client: LLM client with a .chat(messages, sse_queue=, tools=) method.
        sse_queue: Optional SSE queue for token forwarding.
        tools: Optional OpenAI-format tool schemas for native function calling.

    Returns:
        Async callable accepting messages and returning LLMResponse.
    """

    async def llm_call(
        messages: list[dict],
        sse_queue_override: asyncio.Queue | None = None,
        *,
        tools_override: list[dict] | None = None,
        tool_choice: object | None = None,
    ) -> LLMResponse:
        """Call the LLM client with timeout protection.

        Args:
            messages: Conversation messages.
            sse_queue_override: Optional per-call SSE queue.
            tools_override: Optional per-call tool schema override.
            tool_choice: Optional forced tool selection override.

        Returns:
            LLMResponse with text and/or tool calls.
        """
        options = _call_options(
            sse_queue_override=sse_queue_override,
            default_sse_queue=sse_queue,
            tools_override=tools_override,
            default_tools=tools,
        )
        return await _call_with_timeout(
            client,
            messages=messages,
            sse_queue=options["sse_queue"],
            tools=options["tools"],
            tool_choice=tool_choice,
        )

    return llm_call
