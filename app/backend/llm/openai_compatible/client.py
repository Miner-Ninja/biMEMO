"""Shared OpenAI-compatible chat client.

Implements the common wire protocol used by providers that expose
OpenAI-style chat-completions, model listing, streaming chunks, and
usage payloads. Branded provider adapters set their own provider name,
default URLs, source labels, and environment overrides.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, ClassVar

import httpx

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
from backend.llm.openai_compatible.capabilities import (
    DEFAULT_OPENAI_COMPAT_CONTEXT_TOKENS,
    SOURCE_CUSTOM_MODELS,
    SOURCE_ENV_OVERRIDE,
    SOURCE_PROVIDER_DEFAULT,
    SOURCE_UI_OVERRIDE,
    _context_window_from_model_entry,
    _model_entries,
    _model_entry_matches,
    _read_env_override,
)
from backend.llm.openai_compatible.messages import (
    _raw_message_from_response,
    _structured_content_from_message,
    _tool_calls_from_message,
)
from backend.llm.openai_compatible.protocol import (
    CUSTOM_API_PREFIX,
    DEFAULT_OPENAI_API_PREFIX,
    DONE_STREAM_MARKER,
    EMPTY_ASSISTANT_CONTENT,
    OpenAICompatibleClientError,
    _build_auth_headers,
    _chat_payload,
    _chat_response_format,
    _empty_warning_response,
    _is_empty_response,
    _request_messages,
    _should_stream,
    _usage_from_chunk,
    _usage_from_response_body,
    _uses_structured_response,
)
from backend.llm.reasoning import strip_reasoning_output
from backend.llm.response_format import resolve_llm_response_format
from backend.llm.structured_response import (
    StructuredAssistantContent,
    parse_structured_response_json,
)
from backend.llm.structured_stream import AnswerFieldStreamExtractor


if TYPE_CHECKING:
    import asyncio

    from backend.llm.usage import LLMCallUsage


logger = logging.getLogger(__name__)


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
        stream_queue: asyncio.Queue | None = None,
        tools: list[dict] | None = None,
        tool_choice: object | None = None,
        options: LLMCallOptions | None = None,
    ) -> LLMResponse:
        """Stream chat completion and return structured response.

        Supports native OpenAI-compatible function calling via
        the ``tools`` parameter. Tool call deltas are accumulated
        across SSE chunks and returned as ``LLMToolCall`` objects.

        The two-stage structured finalization (Stage 1 TEXT+tools,
        Stage 2 JSON_SCHEMA synthesis) lives one level up in
        ``backend.agent.structured_finalization`` — this client performs
        exactly ONE provider call per invocation. Explicit call controls
        from ``LLMCallOptions`` — ``response_stream``, ``empty_retry``,
        and ``event_sink`` — override
        the default per-format behaviour.

        Args:
            messages: List of message dicts with role/content.
            stream_queue: Optional queue for real-time token forwarding to
                the Web UI over the realtime stream. Ignored when
                ``options.stream_queue`` is set.
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
        effective_stream_queue = (
            effective.stream_queue
            if effective.stream_queue is not None
            else stream_queue
        )
        effective_tools = effective.tools if effective.tools is not None else tools
        effective_tool_choice = (
            effective.tool_choice if effective.tool_choice is not None else tool_choice
        )

        # Resolve explicit call controls from LLMCallOptions.
        response_stream = effective.response_stream
        empty_retry = effective.empty_retry
        event_sink = effective.event_sink

        # Apply event_sink tri-state to the stream queue.
        if event_sink is EventSink.DISABLED:
            effective_stream_queue = None
        elif event_sink is EventSink.ENABLED:
            pass  # keep effective_stream_queue as resolved above
        # INHERIT or None: use the already-resolved effective_stream_queue

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
                    effective_stream_queue,
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

    async def _execute_chat_once(
        self,
        payload: dict[str, object],
        llm_response_format: LlmResponseFormat,
        stream_queue: asyncio.Queue | None,
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
                    stream_queue,
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
            stream_queue,
            llm_response_format,
            phase=phase,
            attempt=attempt,
        )

    async def _streaming_chat(
        self,
        payload: dict[str, object],
        stream_queue: asyncio.Queue | None,
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
                            if answer_delta and stream_queue is not None:
                                await stream_queue.put(
                                    {
                                        "type": "token",
                                        "content": answer_delta,
                                    }
                                )
                        else:
                            full_text.append(content)
                            if stream_queue is not None:
                                await stream_queue.put(
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
                        if stream_queue is not None:
                            await stream_queue.put(
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
        tool_calls = _tool_calls_from_message(message)
        if skip_parsing:
            structured_content = None
            text = str(message.get("content", EMPTY_ASSISTANT_CONTENT))
        else:
            try:
                structured_content = _structured_content_from_message(
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
            raw_message=_raw_message_from_response(
                text=text,
                tool_calls=tool_calls,
            ),
            usage=response_usage,
            llm_response_format=llm_response_format,
            structured_content=structured_content,
        )
