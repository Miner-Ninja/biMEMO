"""Shared OpenAI-compatible chat client.

Implements the common wire protocol used by providers that expose
OpenAI-style chat-completions, model listing, streaming chunks, and
usage payloads. Branded provider adapters set their own provider name,
default URLs, source labels, and environment overrides.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, ClassVar

import httpx

from backend.config import get_settings
from backend.llm.call_factory import (
    AttemptRecord,
    LLMResponse,
    LLMToolCall,
    current_thinking_enabled,
)
from backend.llm.call_options import (
    EmptyRetry,
    EventSink,
    LLMCallOptions,
)
from backend.llm.capabilities import (
    CapabilitySupport,
    LlmResponseFormat,
    ModelCapabilities,
)
from backend.llm.reasoning import strip_reasoning_output
from backend.llm.response_format import resolve_llm_response_format
from backend.llm.structured_response import (
    OPENAI_FINAL_ONLY_RESPONSE_FORMAT,
    OPENAI_STRUCTURED_RESPONSE_FORMAT,
    STRUCTURED_RESPONSE_INSTRUCTION,
    StructuredAssistantContent,
    parse_final_only_response_json,
    parse_structured_response_json,
)
from backend.llm.structured_stream import AnswerFieldStreamExtractor
from backend.llm.usage import LLMCallUsage


if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable


logger = logging.getLogger(__name__)
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
EMPTY_LLM_RESPONSE_WARNING = (
    "LLM returned an empty response. Check your connection to LLM and try again."
)

# --- Capability sources for OpenAI-compatible providers ---
ENV_VAR_CUSTOM_NUM_CTX = "BIMEMO_CUSTOM_NUM_CTX"
SOURCE_ENV_OVERRIDE = "env-override"
SOURCE_UI_OVERRIDE = "ui-override"
SOURCE_CUSTOM_MODELS = "custom_llm:/v1/models"
SOURCE_PROVIDER_DEFAULT = "provider-default"
DEFAULT_OPENAI_COMPAT_CONTEXT_TOKENS = 8192
CONTEXT_WINDOW_FIELD_NAMES = (
    "context_length",
    "context_window",
    "max_context_length",
    "max_context_tokens",
    "max_model_len",
    "max_model_length",
    "max_sequence_length",
    "max_position_embeddings",
    "n_ctx",
)
MODEL_ID_FIELD_NAMES = ("id", "name", "model", "root")


def _read_env_override(env_names: tuple[str, ...]) -> int | None:
    """Read provider env num_ctx variables and return a positive int.

    Treats unset, empty, non-integer, and non-positive values as "unset"
    so the priority chain proceeds to the UI override or provider default.
    """
    for env_name in env_names:
        raw = os.environ.get(env_name)
        if raw is None or raw.strip() == "":
            continue
        try:
            parsed = int(raw)
        except ValueError:
            logger.warning(
                "Invalid integer in %s=%r; ignoring override",
                env_name,
                raw,
            )
            continue
        if parsed > 0:
            return parsed
    return None


def _positive_int(value: object) -> int | None:
    """Return a positive integer parsed from one provider metadata value."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float) and value.is_integer():
        parsed = int(value)
        return parsed if parsed > 0 else None
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.isdecimal():
            parsed = int(normalized)
            return parsed if parsed > 0 else None
    return None


def _context_window_from_model_entry(entry: dict[str, object]) -> int | None:
    """Extract a context-window value from one OpenAI-compatible model entry."""
    for field_name in CONTEXT_WINDOW_FIELD_NAMES:
        parsed = _positive_int(entry.get(field_name))
        if parsed is not None:
            return parsed
    return None


def _model_entry_id_values(entry: dict[str, object]) -> set[str]:
    """Return all known string identifiers for one provider model entry."""
    return {
        value
        for field_name in MODEL_ID_FIELD_NAMES
        if isinstance((value := entry.get(field_name)), str) and value
    }


def _model_entry_matches(entry: dict[str, object], model: str) -> bool:
    """Return whether one provider model entry describes the requested model."""
    return model in _model_entry_id_values(entry)


def _model_entries(response_body: object) -> list[dict[str, object]]:
    """Return OpenAI-style model entries from one ``/models`` response body."""
    if not isinstance(response_body, dict):
        return []
    raw_entries = response_body.get("data")
    if not isinstance(raw_entries, list):
        raw_entries = response_body.get("models")
    if not isinstance(raw_entries, list):
        return []
    return [entry for entry in raw_entries if isinstance(entry, dict)]


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
    response_stream: object | None = None,
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


@dataclass
class OpenAICompatibleClient:
    """OpenAI-compatible LLM client via OpenAI-compatible API.

    Attributes:
        api_key: Provider API key.
        model: Model identifier for chat completion.
        base_url: Provider API base URL.
        api_prefix: API path prefix for provider endpoints.
        num_ctx: Optional configured context window size.
        timeout: Request timeout in seconds.
    """

    structured_capability_declarations: ClassVar[dict[str, CapabilitySupport]] = {}
    name: ClassVar[str] = "openai_compatible"
    env_override_names: ClassVar[tuple[str, ...]] = ()
    models_source: ClassVar[str] = SOURCE_CUSTOM_MODELS

    api_key: str
    model: str
    base_url: str = ""
    api_prefix: str = DEFAULT_OPENAI_API_PREFIX
    num_ctx: int | None = None
    timeout: float = 120.0
    _client: httpx.AsyncClient = field(init=False, repr=False)
    _resolved_context_tokens: int | None = field(
        init=False,
        repr=False,
        default=None,
    )

    def _capability_source_for_models_endpoint(self) -> str:
        """Return the source label for this client's model metadata endpoint."""
        return self.models_source

    def _remember_context_tokens(self, tokens: int) -> int:
        """Store the latest resolved context window for usage accounting."""
        self._resolved_context_tokens = tokens
        return tokens

    def _usage_context_tokens(self) -> int | None:
        """Return the best known context window for token usage records."""
        if self.num_ctx is not None and self.num_ctx > 0:
            return self.num_ctx
        return self._resolved_context_tokens

    async def _detect_context_from_models_endpoint(
        self,
        model: str,
    ) -> tuple[int, str] | None:
        """Detect context window from an OpenAI-compatible ``/models`` endpoint."""
        try:
            response = await self._client.get(f"{self.api_prefix}/models")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            entries = _model_entries(response.json())
        except Exception:
            logger.debug(
                "Failed to detect context window from %s/models",
                self.api_prefix,
                exc_info=True,
            )
            return None

        matched_entries = [
            entry for entry in entries if _model_entry_matches(entry, model)
        ]
        candidates = matched_entries if matched_entries else entries
        if len(candidates) != 1 and not matched_entries:
            return None
        for entry in candidates:
            detected = _context_window_from_model_entry(entry)
            if detected is not None:
                return detected, self._capability_source_for_models_endpoint()
        return None

    async def resolve_capabilities(self, model: str) -> ModelCapabilities:
        """Resolve capabilities via overrides and a conservative fallback.

        The resolution chain consists of the env override, the UI override,
        OpenAI-compatible model metadata, and a conservative provider default.

        Args:
            model: Provider-specific model identifier.

        Returns:
            ``ModelCapabilities`` populated from the first override that
            yields a positive integer.

        """
        env_override = _read_env_override(self.env_override_names)
        if env_override is not None:
            return ModelCapabilities(
                provider=self.name,
                model=model,
                max_context_tokens=self._remember_context_tokens(env_override),
                source=SOURCE_ENV_OVERRIDE,
                llm_response_format=resolve_llm_response_format(
                    provider_name=self.name,
                    model_name=model,
                ),
            )
        if self.num_ctx is not None and self.num_ctx > 0:
            return ModelCapabilities(
                provider=self.name,
                model=model,
                max_context_tokens=self._remember_context_tokens(self.num_ctx),
                source=SOURCE_UI_OVERRIDE,
                llm_response_format=resolve_llm_response_format(
                    provider_name=self.name,
                    model_name=model,
                ),
            )
        detected = await self._detect_context_from_models_endpoint(model)
        if detected is not None:
            detected_tokens, source = detected
            return ModelCapabilities(
                provider=self.name,
                model=model,
                max_context_tokens=self._remember_context_tokens(detected_tokens),
                source=source,
                llm_response_format=resolve_llm_response_format(
                    provider_name=self.name,
                    model_name=model,
                ),
            )
        return ModelCapabilities(
            provider=self.name,
            model=model,
            max_context_tokens=self._remember_context_tokens(
                DEFAULT_OPENAI_COMPAT_CONTEXT_TOKENS,
            ),
            source=SOURCE_PROVIDER_DEFAULT,
            llm_response_format=resolve_llm_response_format(
                provider_name=self.name,
                model_name=model,
            ),
        )

    def __post_init__(self) -> None:
        """Initialize HTTP client with Bearer auth."""
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout),
            headers=_build_auth_headers(self.api_key),
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def chat(
        self,
        messages: list[dict],
        *,
        sse_queue: asyncio.Queue | None = None,
        tools: list[dict] | None = None,
        tool_choice: object | None = None,
        options: LLMCallOptions | None = None,
    ) -> LLMResponse:
        """Stream chat completion and return structured response.

        Supports native OpenAI-compatible function calling via
        the ``tools`` parameter. Tool call deltas are accumulated
        across SSE chunks and returned as ``LLMToolCall`` objects.

        When structured tool execution is active and tools are
        present, a two-stage flow is used: Stage 1 sends TEXT+tools
        (streaming by default); Stage 2 sends JSON_SCHEMA with no tools
        (non-streaming) to obtain structured output from the Stage 1
        response. Explicit call controls from ``LLMCallOptions`` —
        ``response_stream``, ``empty_retry``, and ``event_sink`` — override
        the default per-format behaviour.

        Args:
            messages: List of message dicts with role/content.
            sse_queue: Optional queue for real-time token forwarding to
                the Web UI via SSE. Ignored when ``options.sse_queue`` is
                set.
            tools: Optional OpenAI-format tool schemas. Ignored when
                ``options.tools`` is set.
            tool_choice: Optional forced tool selection override. Ignored
                when ``options.tool_choice`` is set.
            options: Per-call override bundle. Any field set overrides the
                corresponding default for this call only.

        Returns:
            LLMResponse with text and/or tool calls.

        Raises:
            OpenAICompatibleClientError: If chat request fails.
        """
        effective = options or LLMCallOptions()
        effective_sse_queue = (
            effective.sse_queue if effective.sse_queue is not None else sse_queue
        )
        effective_tools = effective.tools if effective.tools is not None else tools
        effective_tool_choice = (
            effective.tool_choice if effective.tool_choice is not None else tool_choice
        )

        # Resolve explicit call controls from LLMCallOptions.
        response_stream = effective.response_stream
        empty_retry = effective.empty_retry
        event_sink = effective.event_sink

        # Apply event_sink tri-state to SSE queue.
        if event_sink is EventSink.DISABLED:
            effective_sse_queue = None
        elif event_sink is EventSink.ENABLED:
            pass  # keep effective_sse_queue as resolved above
        # INHERIT or None: use the already-resolved effective_sse_queue

        # Determine whether to retry on empty response.
        do_empty_retry = True
        if empty_retry is EmptyRetry.DISABLED:
            do_empty_retry = False
        elif empty_retry is EmptyRetry.ENABLED:
            do_empty_retry = True

        llm_response_format = effective.response_format or _chat_response_format(
            provider_name=self.name,
            model_name=self.model,
        )

        # --- Single-call flow ---
        if effective_tools:
            llm_response_format = LlmResponseFormat.TEXT
        request_messages = _request_messages(
            messages=messages,
            api_prefix=self.api_prefix,
            llm_response_format=llm_response_format,
        )
        stream = _should_stream(llm_response_format, response_stream)

        payload = _chat_payload(
            model=self.model,
            messages=request_messages,
            tools=effective_tools,
            llm_response_format=llm_response_format,
            stream=stream,
            schema_override=effective.response_schema,
            thinking_enabled=(
                effective.thinking_enabled
                if effective.thinking_enabled is not None
                else current_thinking_enabled()
            ),
            api_prefix=self.api_prefix,
        )
        if effective_tool_choice is not None and effective_tools:
            payload["tool_choice"] = effective_tool_choice

        skip_structured_parsing = effective.response_schema is not None
        single_phase = effective.phase or "single-call"

        async def _execute_single_attempt(attempt_num: int) -> LLMResponse:
            try:
                result = await self._execute_chat_once(
                    payload,
                    llm_response_format,
                    effective_sse_queue,
                    skip_structured_parsing=skip_structured_parsing,
                    phase=single_phase,
                    attempt=attempt_num,
                )
            except Exception as exc:
                if effective.on_attempt is not None:
                    effective.on_attempt(
                        AttemptRecord(
                            phase=single_phase,
                            attempt=attempt_num,
                            error=str(exc),
                        )
                    )
                raise
            if effective.on_attempt is not None:
                effective.on_attempt(
                    AttemptRecord(
                        phase=single_phase,
                        attempt=attempt_num,
                        response=result,
                        usage=result.usage,
                    )
                )
            return result

        response = await _execute_single_attempt(1)
        if _is_empty_response(response) and do_empty_retry:
            logger.warning(
                "OpenAI-compatible provider returned empty response with no "
                "tool calls; retrying once",
            )
            response = await _execute_single_attempt(2)
            if _is_empty_response(response):
                logger.warning(
                    "OpenAI-compatible provider returned empty response on retry; "
                    "surfacing warning to user",
                )
                return _empty_warning_response(response, llm_response_format)
        return response

    async def _chat_with_structured_finalization(
        self,
        messages: list[dict],
        sse_queue: asyncio.Queue | None,
        tools: list[dict],
        tool_choice: object | None,
        response_format: LlmResponseFormat,
        response_stream: object | None,
        do_empty_retry: bool,
        schema_override: dict | None,
        on_attempt: Callable[[AttemptRecord], None] | None,
        phase: str,
    ) -> LLMResponse:
        """Run Stage 1 (TEXT+tools) then Stage 2 (JSON_SCHEMA, non-streaming).

        Stage 1 uses TEXT response format with tools so the model can
        freely choose between answering and calling tools. If the model
        returns tool calls those are returned directly. If the model
        returns text (no tool calls), Stage 2 resubmits the conversation
        with JSON_SCHEMA format and no tools to force structured output.

        Tool choice from the original call is preserved exactly in Stage 1.
        Stage 2 always omits tools (no tool_choice needed).

        ``on_attempt`` and ``phase`` are threaded through so each HTTP
        attempt (Stage 1, Stage 1 retry, Stage 2, Stage 2 retry) produces
        an AttemptRecord via the callback.
        """
        # Resolve capabilities once and validate before any phase.
        capabilities = await self.resolve_capabilities(self.model)

        # R11: native_tools UNKNOWN/UNSUPPORTED must fail before Stage 1.
        if capabilities.native_tools is not CapabilitySupport.SUPPORTED:
            raise OpenAICompatibleClientError(
                f"Stage 1 requires native tool calling support, "
                f"but {capabilities.native_tools.value} for "
                f"{self.name}/{self.model}"
            )

        # R11: non-streaming custom schema must be SUPPORTED for Stage 2.
        if capabilities.non_streaming_custom_schema is not CapabilitySupport.SUPPORTED:
            raise OpenAICompatibleClientError(
                f"Stage 2 requires non-streaming custom schema support, "
                f"but {capabilities.non_streaming_custom_schema.value} for "
                f"{self.name}/{self.model}"
            )

        def _fire_attempt(attempt_record: AttemptRecord) -> None:
            if on_attempt is not None:
                on_attempt(attempt_record)

        # Stage 1: TEXT + tools + stream
        stage1_format = LlmResponseFormat.TEXT
        stage1_messages = _request_messages(
            messages=messages,
            api_prefix=self.api_prefix,
            llm_response_format=stage1_format,
        )
        stage1_stream = _should_stream(stage1_format, response_stream)

        stage1_payload = _chat_payload(
            model=self.model,
            messages=stage1_messages,
            tools=tools,
            llm_response_format=stage1_format,
            stream=stage1_stream,
            schema_override=None,
            thinking_enabled=current_thinking_enabled(),
            api_prefix=self.api_prefix,
        )
        if tool_choice is not None:
            stage1_payload["tool_choice"] = tool_choice

        stage1_phase = "Stage 1"
        stage1_attempt = 1

        try:
            stage1_response = await self._execute_chat_once(
                stage1_payload,
                stage1_format,
                sse_queue,
                skip_structured_parsing=False,
                phase=stage1_phase,
                attempt=stage1_attempt,
            )
        except Exception as exc:
            _fire_attempt(
                AttemptRecord(
                    phase=stage1_phase,
                    attempt=stage1_attempt,
                    error=str(exc),
                )
            )
            raise
        _fire_attempt(
            AttemptRecord(
                phase=stage1_phase,
                attempt=stage1_attempt,
                response=stage1_response,
                usage=stage1_response.usage,
            )
        )

        # Empty-retry for Stage 1
        if _is_empty_response(stage1_response) and do_empty_retry:
            logger.warning(
                "Stage 1 returned empty response; retrying once",
            )
            stage1_attempt += 1
            try:
                stage1_response = await self._execute_chat_once(
                    stage1_payload,
                    stage1_format,
                    sse_queue,
                    skip_structured_parsing=False,
                    phase=stage1_phase,
                    attempt=stage1_attempt,
                )
            except Exception as exc:
                _fire_attempt(
                    AttemptRecord(
                        phase=stage1_phase,
                        attempt=stage1_attempt,
                        error=str(exc),
                    )
                )
                raise
            _fire_attempt(
                AttemptRecord(
                    phase=stage1_phase,
                    attempt=stage1_attempt,
                    response=stage1_response,
                    usage=stage1_response.usage,
                )
            )
            if _is_empty_response(stage1_response):
                logger.warning(
                    "Stage 1 returned empty response on retry; "
                    "surfacing warning to user",
                )
                return _empty_warning_response(stage1_response, stage1_format)

        # If Stage 1 returned tool calls, return directly (no Stage 2).
        if stage1_response.tool_calls:
            return replace(
                stage1_response,
                usage=(
                    replace(
                        stage1_response.usage,
                        tool_capable=True,
                        tool_used=True,
                    )
                    if stage1_response.usage is not None
                    else None
                ),
            )

        # --- Stage 2: JSON_SCHEMA, non-streaming, no tools ---
        stage2_format = LlmResponseFormat.JSON_SCHEMA

        # Append Stage 1 text as an assistant message for Stage 2 context.
        assistant_msg: dict = {
            "role": "assistant",
            "content": stage1_response.text or None,
        }
        stage2_messages = _request_messages(
            messages=[*messages, assistant_msg],
            api_prefix=self.api_prefix,
            llm_response_format=stage2_format,
        )

        use_final_only_parser = schema_override is None
        stage2_schema = schema_override

        # Stage 2 is always non-streaming with no SSE events.
        stage2_payload = _chat_payload(
            model=self.model,
            messages=stage2_messages,
            tools=None,
            llm_response_format=stage2_format,
            stream=False,
            schema_override=stage2_schema,
            thinking_enabled=current_thinking_enabled(),
            api_prefix=self.api_prefix,
        )
        if use_final_only_parser:
            stage2_payload["response_format"] = OPENAI_FINAL_ONLY_RESPONSE_FORMAT

        # Bounded retry: at most two synthesis HTTP attempts.
        max_attempts = 2
        stage2_phase = "Stage 2"
        for attempt_num in range(1, max_attempts + 1):
            try:
                stage2_response = await self._execute_chat_once(
                    stage2_payload,
                    stage2_format,
                    sse_queue=None,
                    skip_structured_parsing=not use_final_only_parser,
                    use_final_only_parser=use_final_only_parser,
                    phase=stage2_phase,
                    attempt=attempt_num,
                )
            except Exception as exc:
                _fire_attempt(
                    AttemptRecord(
                        phase=stage2_phase,
                        attempt=attempt_num,
                        error=str(exc),
                    )
                )
                raise
            stage2_response = replace(
                stage2_response,
                usage=(
                    replace(
                        stage2_response.usage,
                        tool_capable=True,
                        tool_used=False,
                    )
                    if stage2_response.usage is not None
                    else None
                ),
            )
            # Non-retryable: tool calls with no tools offered — fail closed.
            if stage2_response.tool_calls:
                error = "Stage 2 returned tool calls with no tools offered"
                _fire_attempt(
                    AttemptRecord(
                        phase=stage2_phase,
                        attempt=attempt_num,
                        response=stage2_response,
                        error=error,
                        usage=stage2_response.usage,
                    )
                )
                raise OpenAICompatibleClientError(f"{error}; synthesis failed")

            # Custom schema: caller owns parsing; return directly.
            if not use_final_only_parser:
                _fire_attempt(
                    AttemptRecord(
                        phase=stage2_phase,
                        attempt=attempt_num,
                        response=stage2_response,
                        usage=stage2_response.usage,
                    )
                )
                return stage2_response

            # Final-only: check whether structured content was parsed.
            if stage2_response.structured_content is not None:
                _fire_attempt(
                    AttemptRecord(
                        phase=stage2_phase,
                        attempt=attempt_num,
                        response=stage2_response,
                        usage=stage2_response.usage,
                    )
                )
                return stage2_response

            error = "Stage 2 returned empty or malformed structured output"
            _fire_attempt(
                AttemptRecord(
                    phase=stage2_phase,
                    attempt=attempt_num,
                    response=stage2_response,
                    error=error,
                    usage=stage2_response.usage,
                )
            )

            # Empty or malformed: retry if attempts remain.
            if attempt_num < max_attempts:
                logger.warning(
                    "Stage 2 attempt %d returned empty/malformed; "
                    "retrying (attempt %d of %d)",
                    attempt_num,
                    attempt_num + 1,
                    max_attempts,
                )
                continue

        # All attempts exhausted — fail closed (R7: no fallback to Stage 1).
        raise OpenAICompatibleClientError(
            "Stage 2 synthesis exhausted all attempts without valid structured output"
        )

    async def _execute_chat_once(
        self,
        payload: dict[str, object],
        llm_response_format: LlmResponseFormat,
        sse_queue: asyncio.Queue | None,
        *,
        skip_structured_parsing: bool,
        use_final_only_parser: bool = False,
        phase: str = "",
        attempt: int = 0,
    ) -> LLMResponse:
        """Run one OpenAI-compatible chat round and return the raw response."""
        stream = payload.get("stream", False)
        if _uses_structured_response(llm_response_format):
            if stream:
                return await self._streaming_chat(
                    payload,
                    sse_queue,
                    llm_response_format,
                    structured=True,
                    skip_structured_parsing=skip_structured_parsing,
                    phase=phase,
                    attempt=attempt,
                )
            return await self._structured_chat(
                payload,
                llm_response_format,
                skip_parsing=skip_structured_parsing,
                use_final_only_parser=use_final_only_parser,
                phase=phase,
                attempt=attempt,
            )
        return await self._streaming_chat(
            payload,
            sse_queue,
            llm_response_format,
            phase=phase,
            attempt=attempt,
        )

    async def _streaming_chat(
        self,
        payload: dict[str, object],
        sse_queue: asyncio.Queue | None,
        llm_response_format: LlmResponseFormat,
        *,
        structured: bool = False,
        skip_structured_parsing: bool = False,
        phase: str = "",
        attempt: int = 0,
    ) -> LLMResponse:
        """Run one streaming chat round and return LLMResponse.

        When ``structured`` is true the provider is streaming incremental
        JSON text that matches the JSON-schema response format. In that
        case content deltas are fed to an
        :class:`AnswerFieldStreamExtractor` and only the decoded answer
        characters are forwarded as ``token`` SSE events. After the
        stream closes the full raw JSON text is parsed into a
        :class:`StructuredAssistantContent` so the returned
        :class:`LLMResponse` carries the same shape it would in the
        non-streaming structured path.
        """
        full_text: list[str] = []
        extractor = AnswerFieldStreamExtractor() if structured else None
        thinking_parts: list[str] = []
        # Accumulate streamed tool_calls: index → {id, name, arguments_parts}
        tool_call_acc: dict[int, dict] = {}
        final_usage: LLMCallUsage | None = None

        try:
            async with self._client.stream(
                "POST",
                f"{self.api_prefix}/chat/completions",
                json=payload,
            ) as response:
                if response.status_code != 200:
                    error_body = await response.aread()
                    logger.error(
                        "OpenAI-compatible provider %s: %s",
                        response.status_code,
                        error_body[:1000],
                    )
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == DONE_STREAM_MARKER:
                        break
                    chunk = json.loads(data_str)
                    chunk_usage = _usage_from_chunk(
                        chunk,
                        default_model=self.model,
                        num_ctx=self._usage_context_tokens(),
                        phase=phase,
                        attempt=attempt,
                    )
                    if chunk_usage is not None:
                        final_usage = chunk_usage
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})

                    # Forward visible content immediately to the SSE queue.
                    # The inference server is responsible for splitting visible
                    # content from reasoning content via its own reasoning
                    # parser, so we trust each field as already classified.
                    content = delta.get("content", "")
                    if content:
                        if extractor is not None:
                            answer_delta = extractor.feed(content)
                            if answer_delta and sse_queue is not None:
                                await sse_queue.put(
                                    {
                                        "type": "token",
                                        "content": answer_delta,
                                    }
                                )
                        else:
                            full_text.append(content)
                            if sse_queue is not None:
                                await sse_queue.put(
                                    {
                                        "type": "token",
                                        "content": content,
                                    }
                                )

                    # Forward reasoning content (thinking) when the upstream
                    # provider emits it as a separate channel.
                    reasoning_content = delta.get("reasoning_content", "")
                    if reasoning_content:
                        thinking_parts.append(reasoning_content)
                        if sse_queue is not None:
                            await sse_queue.put(
                                {
                                    "type": "thinking",
                                    "content": reasoning_content,
                                }
                            )

                    # Accumulate tool_calls deltas
                    for tool_call_delta in delta.get("tool_calls", []):
                        tool_call_index = tool_call_delta.get("index", 0)
                        if tool_call_index not in tool_call_acc:
                            tool_call_acc[tool_call_index] = {
                                "id": "",
                                "name": "",
                                "arguments_parts": [],
                            }
                        tool_call_parts = tool_call_acc[tool_call_index]
                        if tool_call_delta.get("id"):
                            tool_call_parts["id"] = tool_call_delta["id"]
                        function_delta = tool_call_delta.get("function", {})
                        if function_delta.get("name"):
                            tool_call_parts["name"] = function_delta["name"]
                        if function_delta.get("arguments"):
                            tool_call_parts["arguments_parts"].append(
                                function_delta["arguments"]
                            )

        except httpx.HTTPStatusError as e:
            try:
                await e.response.aread()
                body = e.response.text[:500]
            except Exception:
                body = "could not read response body"
            logger.error(
                "OpenAI-compatible provider %s: %s",
                e.response.status_code,
                body,
            )
            raise OpenAICompatibleClientError(
                f"Chat request failed: {e.response.status_code} - {body}"
            ) from e
        except httpx.HTTPError as e:
            raise OpenAICompatibleClientError(f"Chat request failed: {e}") from e

        # Build tool calls from accumulated deltas
        tool_calls: list[LLMToolCall] = []
        for tool_call_index in sorted(tool_call_acc):
            tool_call_parts = tool_call_acc[tool_call_index]
            raw_arguments = "".join(tool_call_parts["arguments_parts"])
            # Providers legitimately stream empty arguments for zero-parameter
            # tools; only non-empty malformed JSON is a protocol violation.
            if not raw_arguments.strip():
                arguments = {}
            else:
                try:
                    arguments = json.loads(raw_arguments)
                except json.JSONDecodeError as exc:
                    raise OpenAICompatibleClientError(
                        "Provider returned malformed tool call arguments for "
                        f"{tool_call_parts['name']}"
                    ) from exc
            if not isinstance(arguments, dict):
                raise OpenAICompatibleClientError(
                    "Provider returned non-object tool call arguments for "
                    f"{tool_call_parts['name']}"
                )
            tool_calls.append(
                LLMToolCall(
                    id=tool_call_parts["id"],
                    name=tool_call_parts["name"],
                    arguments=arguments,
                )
            )

        structured_content: StructuredAssistantContent | None = None
        if extractor is not None:
            text = extractor.full_text
            if not skip_structured_parsing and not tool_calls:
                joined_thinking = "".join(thinking_parts) or None
                try:
                    structured_content = parse_structured_response_json(
                        text=text,
                        thinking=joined_thinking,
                    )
                except ValueError as parse_err:
                    logger.warning(
                        "Structured response parsing failed: %s",
                        parse_err,
                    )
                    structured_content = None
        else:
            text = "".join(full_text)
            if self.api_prefix == CUSTOM_API_PREFIX:
                # Defensive cleanup: in the rare case where the upstream
                # provider leaks a raw ``<think>...</think>`` block into the
                # visible channel (no server-side reasoning parser
                # configured), strip it from the text saved to conversation
                # history.
                text = strip_reasoning_output(text)

        # Build raw_message for conversation history
        raw_message: dict = {"role": "assistant"}
        if text:
            raw_message["content"] = text
        else:
            raw_message["content"] = None
        if tool_calls:
            raw_message["tool_calls"] = [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.name,
                        "arguments": json.dumps(tool_call.arguments),
                    },
                }
                for tool_call in tool_calls
            ]

        if structured_content is not None:
            final_usage = (
                replace(
                    final_usage,
                    tool_capable=bool(payload.get("tools")),
                    tool_used=bool(tool_calls),
                )
                if final_usage is not None
                else None
            )
            return LLMResponse(
                text=structured_content.answer,
                tool_calls=tool_calls,
                raw_message=raw_message,
                usage=final_usage,
                llm_response_format=llm_response_format,
                structured_content=structured_content,
            )
        final_usage = (
            replace(
                final_usage,
                tool_capable=bool(payload.get("tools")),
                tool_used=bool(tool_calls),
            )
            if final_usage is not None
            else None
        )
        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            raw_message=raw_message,
            usage=final_usage,
            llm_response_format=llm_response_format,
        )

    def _structured_content_from_message(
        self,
        message: dict[str, object],
        *,
        use_final_only_parser: bool = False,
    ) -> StructuredAssistantContent | None:
        """Build one structured assistant response from a non-streaming reply."""
        tool_calls = self._tool_calls_from_message(message)
        if tool_calls:
            return None
        thinking = self._thinking_from_message(message)
        parser = (
            parse_final_only_response_json
            if use_final_only_parser
            else parse_structured_response_json
        )
        return parser(
            str(message.get("content", EMPTY_ASSISTANT_CONTENT)),
            thinking=thinking,
        )

    def _thinking_from_message(
        self,
        message: dict[str, object],
    ) -> str | None:
        """Return the separate reasoning text when the provider sent it."""
        for key in ("reasoning_content", "reasoning", "thinking"):
            raw_value = message.get(key)
            if isinstance(raw_value, str) and raw_value.strip():
                return raw_value
        return None

    def _tool_calls_from_message(
        self,
        message: dict[str, object],
    ) -> list[LLMToolCall]:
        """Convert one non-streaming OpenAI-style tool call list."""
        raw_tool_calls = message.get("tool_calls")
        if not isinstance(raw_tool_calls, list):
            return []
        tool_calls: list[LLMToolCall] = []
        for tool_call_index, raw_tool_call in enumerate(raw_tool_calls):
            if not isinstance(raw_tool_call, dict):
                continue
            function = raw_tool_call.get("function", {})
            if not isinstance(function, dict):
                continue
            raw_arguments = function.get("arguments", "")
            arguments = self._parsed_tool_arguments(raw_arguments)
            tool_calls.append(
                LLMToolCall(
                    id=str(
                        raw_tool_call.get(
                            "id",
                            f"openai-compatible-call-{tool_call_index}",
                        )
                    ),
                    name=str(function.get("name", "")),
                    arguments=arguments,
                )
            )
        return tool_calls

    def _parsed_tool_arguments(
        self,
        raw_arguments: object,
    ) -> dict:
        """Parse one OpenAI-style tool-call argument payload into a dict."""
        if isinstance(raw_arguments, dict):
            return raw_arguments
        if raw_arguments is None:
            return {}
        if not isinstance(raw_arguments, str):
            raise OpenAICompatibleClientError(
                "Provider returned missing tool call arguments"
            )
        # An empty/whitespace string is how providers encode a call to a
        # zero-parameter tool; treat it as an empty argument object.
        if not raw_arguments.strip():
            return {}
        try:
            parsed_arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise OpenAICompatibleClientError(
                "Provider returned malformed tool call arguments"
            ) from exc
        if isinstance(parsed_arguments, dict):
            return parsed_arguments
        raise OpenAICompatibleClientError(
            "Provider returned non-object tool call arguments"
        )

    def _raw_message_from_response(
        self,
        *,
        text: str,
        tool_calls: list[LLMToolCall],
    ) -> dict[str, object]:
        """Build one assistant message payload for downstream storage."""
        raw_message: dict[str, object] = {
            "role": "assistant",
            "content": text or None,
        }
        if tool_calls:
            raw_message["tool_calls"] = [
                {
                    "id": tool_call.id,
                    "type": "function",
                    "function": {
                        "name": tool_call.name,
                        "arguments": json.dumps(tool_call.arguments),
                    },
                }
                for tool_call in tool_calls
            ]
        return raw_message

    async def _structured_chat(
        self,
        payload: dict[str, object],
        llm_response_format: LlmResponseFormat,
        *,
        skip_parsing: bool = False,
        use_final_only_parser: bool = False,
        phase: str = "",
        attempt: int = 0,
    ) -> LLMResponse:
        """Request one non-streaming structured response.

        When ``skip_parsing`` is ``True``, the default structured-response
        parsing is bypassed and the raw model text is returned on
        ``LLMResponse.text`` with ``structured_content`` left as ``None``.
        The caller owns parsing its custom schema.
        """
        try:
            response = await self._client.post(
                f"{self.api_prefix}/chat/completions",
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            try:
                await error.response.aread()
                body = error.response.text[:500]
            except Exception:
                body = "could not read response body"
            logger.error(
                "OpenAI-compatible provider %s: %s",
                error.response.status_code,
                body,
            )
            raise OpenAICompatibleClientError(
                f"Chat request failed: {error.response.status_code} - {body}"
            ) from error
        except httpx.HTTPError as error:
            raise OpenAICompatibleClientError(
                f"Chat request failed: {error}"
            ) from error

        response_body = response.json()
        choices = response_body.get("choices", [])
        if not isinstance(choices, list) or not choices:
            raise OpenAICompatibleClientError(
                "Structured chat response is missing choices"
            )
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise OpenAICompatibleClientError(
                "Structured chat response has invalid choices"
            )
        message = first_choice.get("message", {})
        if not isinstance(message, dict):
            raise OpenAICompatibleClientError(
                "Structured chat response is missing a message"
            )
        tool_calls = self._tool_calls_from_message(message)
        if skip_parsing:
            structured_content = None
            text = str(message.get("content", EMPTY_ASSISTANT_CONTENT))
        else:
            try:
                structured_content = self._structured_content_from_message(
                    message,
                    use_final_only_parser=use_final_only_parser,
                )
            except ValueError as parse_err:
                logger.warning(
                    "Structured response parsing failed: %s",
                    parse_err,
                )
                structured_content = None
            text = structured_content.answer if structured_content is not None else ""
        response_usage = _usage_from_response_body(
            response_body,
            default_model=self.model,
            num_ctx=self._usage_context_tokens(),
            phase=phase,
            attempt=attempt,
        )
        if response_usage is not None:
            response_usage = replace(
                response_usage,
                tool_capable=bool(payload.get("tools")),
                tool_used=bool(tool_calls),
            )
        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            raw_message=self._raw_message_from_response(
                text=text,
                tool_calls=tool_calls,
            ),
            usage=response_usage,
            llm_response_format=llm_response_format,
            structured_content=structured_content,
        )
