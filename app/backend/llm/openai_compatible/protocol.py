"""OpenAI-compatible wire protocol: request payloads and response helpers.

Pure functions and constants for the OpenAI-style chat-completions wire
format: auth headers, system-message normalization, request payload
construction, streaming/non-streaming usage extraction, and
empty-response handling. No I/O and no provider state: unit-testable in
isolation and shared by the client, the background provider, and the
branded adapters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.config import get_settings
from backend.llm.call_factory import EMPTY_LLM_RESPONSE_WARNING, LLMResponse
from backend.llm.capabilities import LlmResponseFormat
from backend.llm.response_format import resolve_llm_response_format
from backend.llm.structured_response import (
    OPENAI_STRUCTURED_RESPONSE_FORMAT,
    STRUCTURED_RESPONSE_INSTRUCTION,
    StructuredAssistantContent,
)
from backend.llm.usage import LLMCallUsage


if TYPE_CHECKING:
    from backend.llm.call_options import ResponseStream


# --- Wire constants ---
DEFAULT_OPENAI_API_PREFIX = "/api/v1"
AUTHORIZATION_HEADER = "Authorization"
BEARER_PREFIX = "Bearer "
CUSTOM_API_PREFIX = "/v1"
SYSTEM_ROLE = "system"
SYSTEM_SEPARATOR = "\n\n"
ZERO_DURATION_MS = 0.0
DONE_STREAM_MARKER = "[DONE]"
EMPTY_ASSISTANT_CONTENT = ""
TOOL_CHOICE_AUTO = "auto"


class OpenAICompatibleClientError(Exception):
    """Raised when OpenAI-compatible client operations fail."""


def _build_auth_headers(api_key: str) -> dict[str, str]:
    """Return auth headers only when an API key is configured."""
    if not api_key:
        return {}
    return {AUTHORIZATION_HEADER: f"{BEARER_PREFIX}{api_key}"}


def normalize_system_messages(messages: list[dict]) -> list[dict]:
    """Collapse multiple system messages into one leading system message."""
    system_contents = [
        str(message.get("content", ""))
        for message in messages
        if message.get("role") == SYSTEM_ROLE and message.get("content")
    ]
    non_system_messages = [
        message for message in messages if message.get("role") != SYSTEM_ROLE
    ]
    if not system_contents:
        return non_system_messages
    merged_system_message = {
        "role": SYSTEM_ROLE,
        "content": SYSTEM_SEPARATOR.join(system_contents),
    }
    return [merged_system_message, *non_system_messages]


def _chat_payload(
    *,
    model: str,
    messages: list[dict],
    tools: list[dict] | None,
    llm_response_format: LlmResponseFormat,
    stream: bool,
    schema_override: dict | None = None,
    thinking_enabled: bool | None = None,
    api_prefix: str = DEFAULT_OPENAI_API_PREFIX,
) -> dict:
    """Build one OpenAI-compatible chat-completions request body.

    When ``schema_override`` is provided and the effective format is
    ``JSON_SCHEMA``, the override schema replaces the default
    ``OPENAI_STRUCTURED_RESPONSE_FORMAT`` for this call only. The
    ``stream`` flag is set on the request verbatim — the caller decides
    whether the JSON_SCHEMA path should stream incremental chunks or
    block for the full body. When tools are present (native-tool round),
    the ``response_format`` field is omitted so the LLM can use its native
    tool-calling response format instead of guided JSON schema.
    """
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": stream,
    }
    if stream:
        payload["stream_options"] = {"include_usage": True}
    if tools:
        payload["tools"] = tools
        # Default "auto". Per-turn forcing (chat-input chips) is applied by the
        # caller via options.tool_choice, not here.
        payload["tool_choice"] = TOOL_CHOICE_AUTO
    if llm_response_format is LlmResponseFormat.JSON_SCHEMA and not tools:
        payload["response_format"] = _response_format_for(schema_override)
    # Reasoning switch: only self-hosted OpenAI-compatible servers (vLLM)
    # understand chat_template_kwargs; hosted providers get no extra key.
    if thinking_enabled is False and api_prefix == CUSTOM_API_PREFIX:
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    return payload


def _should_stream(
    llm_response_format: LlmResponseFormat,
    response_stream: ResponseStream | None = None,
) -> bool:
    """Return whether one OpenAI-compatible chat request should stream.

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
    if llm_response_format is not LlmResponseFormat.JSON_SCHEMA:
        return True
    return get_settings().llm_response_stream


def _response_format_for(schema_override: dict | None) -> dict[str, object]:
    """Return the OpenAI ``response_format`` object for this call.

    When ``schema_override`` is set, wrap it in a strict ``json_schema``
    response_format; otherwise use the default structured-response form.
    """
    if schema_override is None:
        return OPENAI_STRUCTURED_RESPONSE_FORMAT
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "custom_response",
            "strict": True,
            "schema": schema_override,
        },
    }


def _chat_response_format(
    *,
    provider_name: str,
    model_name: str,
) -> LlmResponseFormat:
    """Resolve the response format used by one chat request."""
    return resolve_llm_response_format(
        provider_name=provider_name,
        model_name=model_name,
    )


def _uses_structured_response(llm_response_format: LlmResponseFormat) -> bool:
    """Return whether one OpenAI-compatible request should use JSON schema output."""
    return llm_response_format is LlmResponseFormat.JSON_SCHEMA


def _request_messages(
    *,
    messages: list[dict],
    api_prefix: str,
    llm_response_format: LlmResponseFormat,
) -> list[dict]:
    """Build the final request message list for one chat call."""
    request_messages = messages
    if _uses_structured_response(llm_response_format):
        request_messages = [
            {"role": SYSTEM_ROLE, "content": STRUCTURED_RESPONSE_INSTRUCTION},
            *request_messages,
        ]
    if api_prefix == CUSTOM_API_PREFIX:
        return normalize_system_messages(request_messages)
    return request_messages


def _usage_from_chunk(
    chunk: dict,
    *,
    default_model: str,
    num_ctx: int | None,
    phase: str = "",
    attempt: int = 0,
) -> LLMCallUsage | None:
    """Build usage data from one streamed chunk when usage is present."""
    chunk_usage = chunk.get("usage")
    if chunk_usage is None:
        return None
    prompt_tokens = chunk_usage.get("prompt_tokens", 0) or 0
    completion_tokens = chunk_usage.get("completion_tokens", 0) or 0
    return LLMCallUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_ms=ZERO_DURATION_MS,
        model=chunk.get("model", default_model),
        context_total=num_ctx,
        context_used=(
            prompt_tokens + completion_tokens if num_ctx is not None else None
        ),
        phase=phase,
        attempt=attempt,
    )


def _usage_from_response_body(
    response_body: dict[str, object],
    *,
    default_model: str,
    num_ctx: int | None,
    phase: str = "",
    attempt: int = 0,
) -> LLMCallUsage | None:
    """Build usage data from one non-streaming OpenAI-style response body."""
    raw_usage = response_body.get("usage")
    if not isinstance(raw_usage, dict):
        return None
    prompt_tokens = int(raw_usage.get("prompt_tokens", 0) or 0)
    completion_tokens = int(raw_usage.get("completion_tokens", 0) or 0)
    return LLMCallUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_ms=ZERO_DURATION_MS,
        model=str(response_body.get("model", default_model)),
        context_total=num_ctx,
        context_used=(
            prompt_tokens + completion_tokens if num_ctx is not None else None
        ),
        phase=phase,
        attempt=attempt,
    )


def _is_empty_response(response: LLMResponse) -> bool:
    """Return whether one chat response has blank text and no tool calls."""
    return not response.text.strip() and not response.tool_calls


def _empty_warning_response(
    source: LLMResponse,
    llm_response_format: LlmResponseFormat,
) -> LLMResponse:
    """Build a final response carrying the empty-retry warning message.

    Preserves ``usage`` from the original response so token accounting
    still reflects the provider's work. ``structured_content`` is set
    only when the effective format is ``JSON_SCHEMA`` so chat UIs that
    render structured replies still see the warning as an assistant
    answer.
    """
    structured_content = (
        StructuredAssistantContent(answer=EMPTY_LLM_RESPONSE_WARNING)
        if _uses_structured_response(llm_response_format)
        else None
    )
    return LLMResponse(
        text=EMPTY_LLM_RESPONSE_WARNING,
        tool_calls=[],
        raw_message={
            "role": "assistant",
            "content": EMPTY_LLM_RESPONSE_WARNING,
        },
        usage=source.usage,
        llm_response_format=llm_response_format,
        structured_content=structured_content,
    )
