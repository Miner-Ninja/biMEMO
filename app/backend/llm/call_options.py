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
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    import asyncio

    from backend.llm.capabilities import LlmResponseFormat


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
    """

    response_format: LlmResponseFormat | None = None
    response_schema: dict | None = None
    tools: list[dict] | None = None
    tool_choice: object | None = None
    sse_queue: asyncio.Queue | None = None
