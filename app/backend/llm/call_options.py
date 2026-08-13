"""Per-call override values for a single LLM invocation.

Provides ``LLMCallOptions`` — a frozen dataclass that carries every
override a caller might want to apply to one LLM call without mutating
the shared client, the capabilities cache, or global configuration.

Every field defaults to ``None`` which means "use the default for this
LLM call". This is the single per-call entry point documented by the
``llm-call-options`` capability spec.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable

    from backend.llm.call_factory import AttemptRecord
    from backend.llm.capabilities import LlmResponseFormat


class EventSink(StrEnum):
    """Whether to forward events to an SSE queue for this call."""

    INHERIT = "inherit"
    ENABLED = "enabled"
    DISABLED = "disabled"


class ResponseStream(StrEnum):
    """Whether to stream the LLM response for this call."""

    INHERIT = "inherit"
    ENABLED = "enabled"
    DISABLED = "disabled"


class EmptyRetry(StrEnum):
    """Whether to retry on an empty LLM response for this call."""

    INHERIT = "inherit"
    ENABLED = "enabled"
    DISABLED = "disabled"


@dataclass(frozen=True)
class LLMCallOptions:
    """Per-call overrides for a single LLM invocation.

    Attributes:
        response_format: Override the effective response format for this
            call only. ``None`` keeps the resolved capability default.
        response_schema: Override the JSON schema used when the effective
            format is ``JSON_SCHEMA``. ``None`` keeps the default
            ``STRUCTURED_RESPONSE_SCHEMA``.
        tools: Override the tool schemas offered to the model for this
            call only. ``None`` keeps the caller-scoped default.
        tool_choice: Override the forced tool-choice directive for this
            call only. ``None`` leaves the provider to auto-select.
        sse_queue: Override the SSE queue target for this call only.
            ``None`` keeps the caller-scoped default.
        event_sink: Override the event-sink tri-state (INHERIT / ENABLED /
            DISABLED) for this call only. ``None`` keeps the default.
        response_stream: Override the response-stream tri-state for this
            call only. ``None`` keeps the default.
        empty_retry: Override the empty-retry tri-state for this call only.
            ``None`` keeps the default.
        thinking_enabled: Override provider-side reasoning for this call only.
            ``None`` keeps the per-turn UI setting.
        on_attempt: Callback invoked after every HTTP attempt, including
            failures without usage data. Used by the two-stage flow to
            record each provider attempt. ``None`` means no callback.
        phase: Phase label for attempt records (e.g. ``"Stage 1"``,
            ``"Stage 2"``). Used with ``on_attempt``.
    """

    response_format: LlmResponseFormat | None = None
    response_schema: dict | None = None
    tools: list[dict] | None = None
    tool_choice: object | None = None
    sse_queue: asyncio.Queue | None = None
    event_sink: EventSink | None = None
    response_stream: ResponseStream | None = None
    empty_retry: EmptyRetry | None = None
    thinking_enabled: bool | None = None
    on_attempt: Callable[[AttemptRecord], None] | None = None
    phase: str = ""
