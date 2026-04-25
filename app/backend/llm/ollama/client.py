"""Ollama client for application-level LLM access via Caddy.

Connects to Ollama through the Caddy reverse proxy or a direct
Ollama endpoint. Bearer token authentication is used when a token
file is available. Provides model management, streaming chat,
an LLM call factory for the gateway orchestrator, and a lazily
constructed OllamaProvider for Dialog Recorder/Summary Distiller background tasks.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import httpx

from backend.config import get_settings
from backend.llm.call_factory import (
    LLM_CALL_TIMEOUT,
    LLMResponse,
    LLMToolCall,
)
from backend.llm.call_options import LLMCallOptions
from backend.llm.capabilities import (
    ContextDetectionError,
    LlmResponseFormat,
    ModelCapabilities,
)
from backend.llm.ollama.capabilities import (
    OllamaCapabilityError,
    detect_ollama_model_capabilities,
)
from backend.llm.ollama.messages import convert_messages_for_ollama
from backend.llm.reasoning import strip_reasoning_output
from backend.llm.response_format import resolve_llm_response_format
from backend.llm.structured_response import (
    STRUCTURED_RESPONSE_INSTRUCTION,
    STRUCTURED_RESPONSE_SCHEMA,
    StructuredAssistantContent,
    parse_structured_response_json,
)
from backend.llm.structured_stream import AnswerFieldStreamExtractor
from backend.llm.usage import LLMCallUsage


if TYPE_CHECKING:
    import asyncio

    from backend.llm.capabilities_registry import CapabilitiesRegistry
    from backend.llm.compute_config import (
        ComputeConfig,
    )
    from backend.llm.hardware import ProfileConfig
    from backend.llm.ollama.provider import (
        OllamaProvider,
    )


logger = logging.getLogger(__name__)

EMPTY_LLM_RESPONSE_WARNING = (
    "LLM returned an empty response. Check your connection to LLM and try again."
)


class OllamaClientError(Exception):
    """Raised when Ollama client operations fail."""


@dataclass
class OllamaClient:
    """Application-level Ollama client via Caddy proxy.

    Connects to Ollama through Caddy or a direct Ollama
    endpoint. Bearer token authentication is enabled only
    when a tmpfs token file is available. Exposes a
    lazily constructed OllamaProvider via the `provider`
    property for Dialog Recorder/Summary Distiller background tasks.

    Attributes:
        base_url: Ollama API base URL for primary chat work.
        dialog_recorder_base_url: Optional Ollama API base URL
            for Dialog Recorder/Summary Distiller work.
        model: Primary model name for chat completion.
        token_path: Optional path to Bearer token file on tmpfs.
        num_ctx: Optional explicit context window size for Ollama requests.
        timeout: Request timeout in seconds.
        profile_config: Hardware profile configuration.
        compute_config: GPU/CPU compute configuration.
    """

    name: ClassVar[str] = "ollama"

    base_url: str
    model: str
    capabilities_registry: CapabilitiesRegistry
    dialog_recorder_base_url: str | None = None
    token_path: str | None = None
    num_ctx: int | None = None
    timeout: float = LLM_CALL_TIMEOUT
    profile_config: ProfileConfig | None = None
    compute_config: ComputeConfig | None = None
    _client: httpx.AsyncClient = field(init=False, repr=False)
    _token: str = field(init=False, repr=False, default="")
    _provider: OllamaProvider | None = field(init=False, repr=False, default=None)

    def _load_bearer_headers(self) -> dict[str, str]:
        """Load optional Bearer auth headers from the configured token file."""
        headers: dict[str, str] = {}
        if not self.token_path:
            return headers
        token_file = Path(self.token_path)
        if not token_file.exists():
            logger.warning(
                "Ollama token file %s not found; continuing without Bearer auth",
                self.token_path,
            )
            return headers
        self._token = token_file.read_text().strip()
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def __post_init__(self) -> None:
        """Initialize HTTP client with Bearer auth."""
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout),
            headers=self._load_bearer_headers(),
        )

    def _validate_provider_dependencies(self) -> None:
        """Validate that provider construction has the required configs."""
        if self.profile_config is None:
            raise OllamaClientError(
                "profile_config must be set before accessing provider"
            )

    def _build_provider(self) -> OllamaProvider:
        """Build one OllamaProvider instance from the current config."""
        from backend.llm.compute_config import default_compute_config
        from backend.llm.ollama.provider import (
            OllamaProvider,
        )

        self._validate_provider_dependencies()
        return OllamaProvider(
            base_url=self.base_url,
            dialog_recorder_base_url=self.dialog_recorder_base_url,
            primary_model=(self.profile_config.primary_model),
            dialog_recorder_model=(self.profile_config.dialog_recorder_model),
            compute_config=self.compute_config or default_compute_config(),
            num_ctx=self.num_ctx,
            capabilities_registry=self.capabilities_registry,
        )

    @property
    def provider(self) -> OllamaProvider:
        """Return a lazily constructed OllamaProvider.

        Constructs the provider on first access using
        profile_config and compute_config, then caches it.

        Returns:
            Configured OllamaProvider instance.

        Raises:
            OllamaClientError: If profile_config is not set.
        """
        if self._provider is not None:
            return self._provider
        self._provider = self._build_provider()
        return self._provider

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def resolve_capabilities(self, model: str) -> ModelCapabilities:
        """Resolve the available context window for ``model``.

        Runs the override-or-detect priority chain: env override, then UI
        override (``self.num_ctx``), then provider-native warmup
        detection. Raises ``ContextDetectionError`` on total failure.

        Args:
            model: Provider-specific model identifier.

        Returns:
            A ``ModelCapabilities`` carrying the resolved context window
            and a ``source`` string identifying which step won.

        Raises:
            ContextDetectionError: If neither override nor warmup
                detection produced a positive integer.
        """
        # Lazy import avoids a hard dep on provider module constants here.
        from backend.llm.ollama.provider import (
            SOURCE_ENV_OVERRIDE,
            SOURCE_UI_OVERRIDE,
            _read_env_override,
        )

        env_override = _read_env_override()
        if env_override is not None:
            return ModelCapabilities(
                provider=self.name,
                model=model,
                max_context_tokens=env_override,
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
                max_context_tokens=self.num_ctx,
                source=SOURCE_UI_OVERRIDE,
                llm_response_format=resolve_llm_response_format(
                    provider_name=self.name,
                    model_name=model,
                ),
            )
        try:
            detected = await detect_ollama_model_capabilities(self._client, model)
        except OllamaCapabilityError as exc:
            raise ContextDetectionError(
                provider=self.name,
                model=model,
                attempted_sources=("ollama:/api/ps", "ollama:/api/show"),
                cause=exc,
            ) from exc
        return ModelCapabilities(
            provider=self.name,
            model=model,
            max_context_tokens=detected.runtime_context_tokens,
            source=detected.source,
            llm_response_format=resolve_llm_response_format(
                provider_name=self.name,
                model_name=model,
            ),
        )

    async def check_model(self) -> bool:
        """Check if configured model is available.

        Returns:
            True if model is found in Ollama's model list.

        Raises:
            OllamaClientError: If Ollama is unreachable.
        """
        try:
            models = await self._list_model_names()
            return self._is_configured_model_present(models)
        except httpx.HTTPError as e:
            raise OllamaClientError(f"Failed to check models: {e}") from e

    async def _list_model_names(self) -> list[str]:
        """Load the list of available Ollama model names."""
        response = await self._client.get("/api/tags")
        response.raise_for_status()
        data = response.json()
        return [model_data["name"] for model_data in data.get("models", [])]

    def _is_configured_model_present(self, models: list[str]) -> bool:
        """Return True when the configured model is present in the model list."""
        for name in models:
            if name == self.model or name == f"{self.model}:latest":
                return True
        return False

    async def pull_model(self) -> None:
        """Pull the configured model from Ollama registry.

        Streams progress and logs updates until the pull
        completes.

        Raises:
            OllamaClientError: If pull fails.
        """
        logger.info("Pulling model %s...", self.model)
        try:
            await self._stream_model_pull()
        except httpx.HTTPError as e:
            raise OllamaClientError(f"Model pull failed: {e}") from e
        logger.info("Model %s pulled successfully", self.model)

    async def _stream_model_pull(self) -> None:
        """Stream and log Ollama model pull progress."""
        async with self._client.stream(
            "POST",
            "/api/pull",
            json={"name": self.model},
            timeout=httpx.Timeout(None),
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                self._log_pull_progress(json.loads(line))

    def _log_pull_progress(self, chunk: dict[str, object]) -> None:
        """Log one pull progress chunk from Ollama."""
        status = chunk.get("status", "")
        completed = chunk.get("completed", 0)
        total = chunk.get("total", 0)
        if total > 0:
            pct = (completed / total) * 100
            logger.info(
                "Pull progress: %s (%.1f%%)",
                status,
                pct,
            )
            return
        if status:
            logger.info("Pull: %s", status)

    def _build_options(self, capabilities: ModelCapabilities) -> dict[str, int]:
        """Build Ollama request options with mandatory ``num_ctx``.

        Args:
            capabilities: Resolved capabilities for the target model. The
                ``max_context_tokens`` value is always written into
                ``options.num_ctx`` so Ollama cannot fall back to its
                internal 4096 default.
        """
        return {"num_ctx": capabilities.max_context_tokens}

    def _uses_structured_response(
        self,
        capabilities: ModelCapabilities,
    ) -> bool:
        """Return whether this Ollama request should use JSON schema output."""
        return capabilities.llm_response_format is LlmResponseFormat.JSON_SCHEMA

    def _should_stream(self, capabilities: ModelCapabilities) -> bool:
        """Return whether this Ollama request should stream token chunks.

        TEXT mode always streams. JSON_SCHEMA mode streams only when the
        operator opted in via ``BIMEMO_LLM_RESPONSE_STREAM=true``.
        """
        if not self._uses_structured_response(capabilities):
            return True
        return get_settings().llm_response_stream

    def _structured_response_messages(
        self,
        messages: list[dict],
    ) -> list[dict]:
        """Append the structured-response instruction to one chat request."""
        return [
            {"role": "system", "content": STRUCTURED_RESPONSE_INSTRUCTION},
            *messages,
        ]

    def _build_chat_payload(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        *,
        capabilities: ModelCapabilities,
        schema_override: dict | None = None,
    ) -> dict[str, object]:
        """Build the Ollama chat request payload.

        Capabilities are required: it is syntactically impossible to
        construct a payload without a known context window. When
        ``schema_override`` is provided and the effective format is
        ``JSON_SCHEMA``, that schema replaces ``STRUCTURED_RESPONSE_SCHEMA``
        in the HTTP payload for this call only.
        """
        request_messages = (
            self._structured_response_messages(messages)
            if self._uses_structured_response(capabilities)
            else messages
        )
        payload: dict[str, object] = {
            "model": self.model,
            "messages": convert_messages_for_ollama(request_messages),
            "stream": self._should_stream(capabilities),
            "options": self._build_options(capabilities),
        }
        if self._uses_structured_response(capabilities):
            payload["format"] = schema_override or STRUCTURED_RESPONSE_SCHEMA
        if tools:
            payload["tools"] = tools
        return payload

    async def _emit_reasoning_deltas(
        self,
        sse_queue: asyncio.Queue | None,
        *,
        visible_delta: str,
        thinking_delta: str,
    ) -> None:
        """Forward visible and hidden reasoning deltas to the SSE queue."""
        if sse_queue is None:
            return
        if thinking_delta:
            await sse_queue.put(
                {
                    "type": "thinking",
                    "content": thinking_delta,
                }
            )
        if visible_delta:
            await sse_queue.put(
                {
                    "type": "token",
                    "content": visible_delta,
                }
            )

    def _extract_tool_calls(
        self,
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
        self,
        chunk: dict[str, object],
        capabilities: ModelCapabilities,
    ) -> LLMCallUsage:
        """Build usage data from the final Ollama done chunk.

        Args:
            chunk: Final ``done=True`` chunk from the Ollama stream.
            capabilities: Resolved capabilities for this chat call. Its
                ``max_context_tokens`` populates ``context_total`` so the
                UI usage indicator reflects the value actually sent.
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
            generation_ms=(
                (chunk.get("eval_duration") or 0) / nanoseconds_to_milliseconds
            ),
            total_ms=((chunk.get("total_duration") or 0) / nanoseconds_to_milliseconds),
            model=chunk.get("model", self.model),
            context_total=capabilities.max_context_tokens,
            context_used=prompt_tokens + completion_tokens,
        )

    async def _stream_chat_chunks(
        self,
        payload: dict[str, object],
        sse_queue: asyncio.Queue | None,
        capabilities: ModelCapabilities,
    ) -> tuple[list[str], list[LLMToolCall], LLMCallUsage | None]:
        """Stream Ollama chat chunks and collect text, tool calls, and usage.

        Reads ``message.content`` and ``message.thinking`` as separate
        channels and forwards them to the SSE queue without buffering.
        Ollama 0.5+ splits visible content from reasoning content on the
        server side, so the application no longer parses ``<think>`` markers.

        ``capabilities`` is required so the final usage record can carry
        the resolved ``context_total`` consumed by the UI memory zones.
        """
        full_text: list[str] = []
        tool_calls: list[LLMToolCall] = []
        final_usage: LLMCallUsage | None = None
        async with self._client.stream(
            "POST",
            "/api/chat",
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                message = chunk.get("message", {})
                content = message.get("content", "")
                thinking = message.get("thinking", "")
                if content:
                    full_text.append(content)
                if content or thinking:
                    await self._emit_reasoning_deltas(
                        sse_queue,
                        visible_delta=content,
                        thinking_delta=thinking,
                    )
                if message.get("tool_calls"):
                    tool_calls.extend(self._extract_tool_calls(message))
                if chunk.get("done", False):
                    final_usage = await self._build_usage_from_done_chunk(
                        chunk,
                        capabilities,
                    )
                    break

        return full_text, tool_calls, final_usage

    async def _request_structured_chat(
        self,
        payload: dict[str, object],
        capabilities: ModelCapabilities,
    ) -> tuple[str, str, list[LLMToolCall], LLMCallUsage | None]:
        """Request one non-streaming structured Ollama chat response."""
        response = await self._client.post("/api/chat", json=payload)
        response.raise_for_status()
        chunk = response.json()
        message = chunk.get("message", {})
        content = str(message.get("content", ""))
        thinking = str(message.get("thinking", ""))
        tool_calls = self._extract_tool_calls(message)
        final_usage = await self._build_usage_from_done_chunk(chunk, capabilities)
        return content, thinking, tool_calls, final_usage

    async def _stream_structured_chat(
        self,
        payload: dict[str, object],
        sse_queue: asyncio.Queue | None,
        capabilities: ModelCapabilities,
    ) -> tuple[str, str, list[LLMToolCall], LLMCallUsage | None]:
        """Stream a JSON-schema response and emit decoded answer deltas.

        Accumulates raw ``message.content`` chunks (which arrive as
        partial JSON text) into an :class:`AnswerFieldStreamExtractor`
        and forwards the newly-decoded characters of the ``answer``
        field as ``token`` SSE events. Reasoning deltas from Ollama's
        separate ``message.thinking`` channel continue to flow as
        ``thinking`` SSE events, unchanged from the TEXT path.

        Returns the full accumulated raw JSON text (for the caller's
        final ``parse_structured_response_json`` call), the full
        thinking text, any tool calls, and the final usage record.
        """
        extractor = AnswerFieldStreamExtractor()
        thinking_parts: list[str] = []
        tool_calls: list[LLMToolCall] = []
        final_usage: LLMCallUsage | None = None
        async with self._client.stream(
            "POST",
            "/api/chat",
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                message = chunk.get("message", {})
                content = str(message.get("content", ""))
                thinking = str(message.get("thinking", ""))
                answer_delta = extractor.feed(content) if content else ""
                if thinking:
                    thinking_parts.append(thinking)
                if answer_delta or thinking:
                    await self._emit_reasoning_deltas(
                        sse_queue,
                        visible_delta=answer_delta,
                        thinking_delta=thinking,
                    )
                if message.get("tool_calls"):
                    tool_calls.extend(self._extract_tool_calls(message))
                if chunk.get("done", False):
                    final_usage = await self._build_usage_from_done_chunk(
                        chunk,
                        capabilities,
                    )
                    break
        return (
            extractor.full_text,
            "".join(thinking_parts),
            tool_calls,
            final_usage,
        )

    def _structured_content_from_ollama(
        self,
        *,
        text: str,
        thinking: str,
        tool_calls: list[LLMToolCall],
    ) -> StructuredAssistantContent | None:
        """Build structured assistant content from one Ollama JSON response."""
        if tool_calls:
            return None
        return parse_structured_response_json(
            text,
            thinking=thinking,
        )

    def _final_visible_text(self, full_text: list[str]) -> str:
        """Return the assembled visible text with a defensive ``<think>`` strip.

        With server-side reasoning separation this is normally a no-op, but it
        protects the saved conversation history when an upstream Ollama version
        leaks a raw ``<think>...</think>`` block into the visible content.
        """
        return strip_reasoning_output("".join(full_text))

    def _raw_assistant_message(
        self,
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

    async def _resolve_for_chat(self) -> ModelCapabilities:
        """Resolve capabilities for the configured model via the registry."""
        return await self.capabilities_registry.get_or_detect(self, self.model)

    async def _execute_chat_once(
        self,
        payload: dict[str, object],
        capabilities: ModelCapabilities,
        sse_queue: asyncio.Queue | None,
        *,
        skip_structured_parsing: bool,
    ) -> tuple[
        str,
        list[LLMToolCall],
        LLMCallUsage | None,
        StructuredAssistantContent | None,
    ]:
        """Run one Ollama chat round and return its text + tool calls + usage.

        Raises ``httpx.HTTPError`` on transport failure; the caller wraps
        it in ``OllamaClientError``.
        """
        if self._uses_structured_response(capabilities):
            if self._should_stream(capabilities):
                (
                    raw_text,
                    raw_thinking,
                    tool_calls,
                    final_usage,
                ) = await self._stream_structured_chat(
                    payload,
                    sse_queue,
                    capabilities,
                )
            else:
                (
                    raw_text,
                    raw_thinking,
                    tool_calls,
                    final_usage,
                ) = await self._request_structured_chat(payload, capabilities)
            if skip_structured_parsing:
                return raw_text, tool_calls, final_usage, None
            structured_content = self._structured_content_from_ollama(
                text=raw_text,
                thinking=raw_thinking,
                tool_calls=tool_calls,
            )
            text = structured_content.answer if structured_content else ""
            return text, tool_calls, final_usage, structured_content
        full_text, tool_calls, final_usage = await self._stream_chat_chunks(
            payload,
            sse_queue,
            capabilities,
        )
        text = self._final_visible_text(full_text)
        return text, tool_calls, final_usage, None

    async def chat(
        self,
        messages: list[dict],
        *,
        sse_queue: asyncio.Queue | None = None,
        tools: list[dict] | None = None,
        options: LLMCallOptions | None = None,
    ) -> LLMResponse:
        """Stream chat completion and return structured response.

        Resolves the model's context window via the capabilities registry
        before each call and writes the value into ``options.num_ctx`` so
        Ollama cannot truncate the prompt to its internal default.

        Args:
            messages: List of message dicts with role/content.
            sse_queue: Optional queue for real-time token forwarding to
                the Web UI via SSE. Ignored when ``options.sse_queue`` is
                set.
            tools: Optional OpenAI-format tool schemas. Ignored when
                ``options.tools`` is set.
            options: Per-call override bundle. Any field set overrides the
                corresponding default for this call only.

        Returns:
            LLMResponse wrapping the complete text.

        Raises:
            OllamaClientError: If chat request fails or capabilities
                cannot be resolved.
        """
        effective = options or LLMCallOptions()
        effective_sse_queue = (
            effective.sse_queue if effective.sse_queue is not None else sse_queue
        )
        effective_tools = effective.tools if effective.tools is not None else tools
        capabilities = await self._resolve_for_chat()
        if effective.response_format is not None:
            capabilities = replace(
                capabilities,
                llm_response_format=effective.response_format,
            )
        payload = self._build_chat_payload(
            messages,
            effective_tools,
            capabilities=capabilities,
            schema_override=effective.response_schema,
        )
        skip_structured_parsing = effective.response_schema is not None
        try:
            (
                text,
                tool_calls,
                final_usage,
                structured_content,
            ) = await self._execute_chat_once(
                payload,
                capabilities,
                effective_sse_queue,
                skip_structured_parsing=skip_structured_parsing,
            )
            if not text.strip() and not tool_calls:
                logger.warning(
                    "Ollama returned empty response with no tool calls; retrying once",
                )
                (
                    text,
                    tool_calls,
                    final_usage,
                    structured_content,
                ) = await self._execute_chat_once(
                    payload,
                    capabilities,
                    effective_sse_queue,
                    skip_structured_parsing=skip_structured_parsing,
                )
                if not text.strip() and not tool_calls:
                    logger.warning(
                        "Ollama returned empty response on retry; "
                        "surfacing warning to user",
                    )
                    text = EMPTY_LLM_RESPONSE_WARNING
                    if (
                        self._uses_structured_response(capabilities)
                        and not skip_structured_parsing
                    ):
                        structured_content = StructuredAssistantContent(
                            answer=EMPTY_LLM_RESPONSE_WARNING,
                        )
                    else:
                        structured_content = None
        except httpx.HTTPError as e:
            raise OllamaClientError(f"Chat request failed: {e}") from e
        raw_message = self._raw_assistant_message(text, tool_calls)
        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            raw_message=raw_message,
            usage=final_usage,
            llm_response_format=capabilities.llm_response_format,
            structured_content=structured_content,
        )
