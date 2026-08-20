"""Ollama wire protocol: request payloads and response helpers.

Pure functions and constants for Ollama's ``/api/chat`` wire format:
request options, payload construction, streaming-chunk parsing, usage
extraction, and structured-response decoding. No I/O and no provider
state: unit-testable in isolation and shared by the client, the
background provider, and the branded adapters.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from backend.config import get_settings
from backend.llm.call_factory import LLMToolCall, current_thinking_enabled
from backend.llm.capabilities import CapabilitySupport, LlmResponseFormat
from backend.llm.ollama.messages import convert_messages_for_ollama
from backend.llm.ollama.models import OllamaClientError
from backend.llm.reasoning import strip_reasoning_output
from backend.llm.structured_response import (
    STRUCTURED_RESPONSE_INSTRUCTION,
    STRUCTURED_RESPONSE_SCHEMA,
    StructuredAssistantContent,
    normalize_legacy_response_text,
    parse_final_only_response_json,
    parse_structured_response_json,
)
from backend.llm.usage import LLMCallUsage


if TYPE_CHECKING:
    import asyncio

    from backend.llm.capabilities import ModelCapabilities


logger = logging.getLogger(__name__)


def _build_options(capabilities: ModelCapabilities) -> dict[str, int]:
    """Build Ollama request options with mandatory ``num_ctx``.

    Args:
        capabilities: Resolved capabilities for the target model. The
            ``max_context_tokens`` value is always written into
            ``options.num_ctx`` so Ollama cannot fall back to its
            internal 4096 default.
    """
    return {"num_ctx": capabilities.max_context_tokens}


def _uses_structured_response(
    capabilities: ModelCapabilities,
) -> bool:
    """Return whether this Ollama request should use JSON schema output."""
    return capabilities.llm_response_format is LlmResponseFormat.JSON_SCHEMA


def _should_stream(
    capabilities: ModelCapabilities,
    response_stream: object | None = None,
) -> bool:
    """Return whether this Ollama request should stream token chunks.

    When ``response_stream`` is explicitly set (from ``LLMCallOptions``),
    it overrides the default per-format behaviour. TEXT mode streams by
    default; JSON_SCHEMA streams only when the operator opted in via
    ``BIMEMO_LLM_RESPONSE_STREAM=true``.
    """
    if response_stream is not None:
        if isinstance(response_stream, str):
            if response_stream in ("enabled", "true", "True", "1"):
                return True
            if response_stream in ("disabled", "false", "False", "0"):
                return False
            # "inherit" or unrecognized: fall through to default logic
        else:
            return bool(response_stream)
    if not _uses_structured_response(capabilities):
        return True
    return get_settings().llm_response_stream


def _structured_response_messages(
    messages: list[dict],
) -> list[dict]:
    """Append the structured-response instruction to one chat request."""
    return [
        {"role": "system", "content": STRUCTURED_RESPONSE_INSTRUCTION},
        *messages,
    ]


def _build_chat_payload(
    model: str,
    messages: list[dict],
    tools: list[dict] | None,
    *,
    capabilities: ModelCapabilities,
    schema_override: dict | None = None,
    tool_choice: object | None = None,
    response_stream: object | None = None,
    thinking_enabled: bool | None = None,
) -> dict[str, object]:
    """Build the Ollama chat request payload.

    Capabilities are required: it is syntactically impossible to
    construct a payload without a known context window. When
    ``schema_override`` is provided and the effective format is
    ``JSON_SCHEMA``, that schema replaces ``STRUCTURED_RESPONSE_SCHEMA``
    in the HTTP payload for this call only.
    """
    request_messages = (
        _structured_response_messages(messages)
        if _uses_structured_response(capabilities)
        else messages
    )
    payload: dict[str, object] = {
        "model": model,
        "messages": convert_messages_for_ollama(request_messages),
        "stream": _should_stream(capabilities, response_stream),
        "options": _build_options(capabilities),
    }
    # Reasoning switch: Ollama's native flag is a TOP-LEVEL "think"
    # key (a sibling of "stream", not inside "options").
    effective_thinking = (
        thinking_enabled if thinking_enabled is not None else current_thinking_enabled()
    )
    if effective_thinking is False:
        payload["think"] = False
    if _uses_structured_response(capabilities) and not tools:
        payload["format"] = schema_override or STRUCTURED_RESPONSE_SCHEMA
    if tools:
        payload["tools"] = tools
        if tool_choice is not None:
            if capabilities.exact_named_choice is not CapabilitySupport.SUPPORTED:
                raise OllamaClientError(
                    "Exact named tool choice required but "
                    f"{capabilities.exact_named_choice.value} for "
                    f"{capabilities.provider}/{capabilities.model}"
                )
            payload["tool_choice"] = tool_choice
        else:
            payload["tool_choice"] = "auto"
    return payload


async def _emit_reasoning_deltas(
    stream_queue: asyncio.Queue | None,
    *,
    visible_delta: str,
    thinking_delta: str,
) -> None:
    """Forward visible and hidden reasoning deltas to the stream queue."""
    if stream_queue is None:
        return
    if thinking_delta:
        await stream_queue.put(
            {
                "type": "thinking",
                "content": thinking_delta,
            }
        )
    if visible_delta:
        await stream_queue.put(
            {
                "type": "token",
                "content": visible_delta,
            }
        )


def _extract_tool_calls(
    message: dict[str, object],
) -> list[LLMToolCall]:
    """Convert one Ollama chunk tool-call list into LLMToolCall objects."""
    parsed_tool_calls: list[LLMToolCall] = []
    for tool_call_index, tool_call_data in enumerate(message.get("tool_calls", [])):
        function = tool_call_data.get("function", {})
        arguments = function.get("arguments", {})
        if not isinstance(arguments, dict):
            logger.warning(
                "Ignoring Ollama tool call with non-dict arguments for %s",
                function.get("name", ""),
            )
            arguments = {}
        parsed_tool_calls.append(
            LLMToolCall(
                id=tool_call_data.get(
                    "id",
                    f"ollama-call-{tool_call_index}",
                ),
                name=function.get("name", ""),
                arguments=arguments,
            )
        )
    return parsed_tool_calls


async def _build_usage_from_done_chunk(
    chunk: dict[str, object],
    capabilities: ModelCapabilities,
    *,
    model: str,
    phase: str = "",
    attempt: int = 0,
) -> LLMCallUsage:
    """Build usage data from the final Ollama done chunk.

    Args:
        chunk: Final ``done=True`` chunk from the Ollama stream.
        capabilities: Resolved capabilities for this chat call. Its
            ``max_context_tokens`` populates ``context_total`` so the
            UI usage indicator reflects the value actually sent.
        model: Model identifier used when the done chunk omits one.
        phase: Logical two-stage phase for usage attribution.
        attempt: Provider-attempt number within the phase.
    """
    prompt_tokens = chunk.get("prompt_eval_count") or 0
    completion_tokens = chunk.get("eval_count") or 0
    nanoseconds_to_milliseconds = 1_000_000
    return LLMCallUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        prompt_ms=(
            (chunk.get("prompt_eval_duration") or 0) / nanoseconds_to_milliseconds
        ),
        generation_ms=((chunk.get("eval_duration") or 0) / nanoseconds_to_milliseconds),
        total_ms=((chunk.get("total_duration") or 0) / nanoseconds_to_milliseconds),
        model=chunk.get("model", model),
        context_total=capabilities.max_context_tokens,
        context_used=prompt_tokens + completion_tokens,
        phase=phase,
        attempt=attempt,
    )


def _structured_content_from_ollama(
    *,
    text: str,
    thinking: str,
    tool_calls: list[LLMToolCall],
    use_final_only_parser: bool = False,
) -> StructuredAssistantContent | None:
    """Build structured assistant content from one Ollama JSON response."""
    if tool_calls:
        return None
    parser = (
        parse_final_only_response_json
        if use_final_only_parser
        else parse_structured_response_json
    )
    try:
        return parser(
            text,
            thinking=thinking,
        )
    except json.JSONDecodeError, ValueError:
        if use_final_only_parser:
            logger.warning(
                "Ollama final-only response failed strict parsing",
                exc_info=True,
            )
            return None
        logger.warning(
            "Ollama returned non-JSON structured response; using plaintext fallback",
            exc_info=True,
        )
        structured_content = normalize_legacy_response_text(text)
        return StructuredAssistantContent(
            answer=structured_content.answer,
            thinking=thinking.strip() if thinking else None,
            suggestions=structured_content.suggestions,
        )


def _final_visible_text(full_text: list[str]) -> str:
    """Return the assembled visible text with a defensive ``<think>`` strip.

    With server-side reasoning separation this is normally a no-op, but it
    protects the saved conversation history when an upstream Ollama version
    leaks a raw ``<think>...</think>`` block into the visible content.
    """
    return strip_reasoning_output("".join(full_text))


def _raw_assistant_message(
    text: str,
    tool_calls: list[LLMToolCall],
) -> dict[str, object]:
    """Build the raw assistant message payload for downstream storage."""
    raw_message: dict[str, object] = {"role": "assistant", "content": text}
    if tool_calls:
        raw_message["tool_calls"] = [
            {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.name,
                    "arguments": tool_call.arguments,
                },
            }
            for tool_call in tool_calls
        ]
    return raw_message
