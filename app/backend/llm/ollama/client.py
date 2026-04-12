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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import httpx

from backend.llm.call_factory import (
    LLM_CALL_TIMEOUT,
    LLMResponse,
    LLMToolCall,
)
from backend.llm.capabilities import ContextDetectionError, ModelCapabilities
from backend.llm.ollama.capabilities import (
    OllamaCapabilityError,
    detect_ollama_model_capabilities,
)
from backend.llm.ollama.messages import convert_messages_for_ollama
from backend.llm.reasoning import strip_reasoning_output
from backend.llm.response_format import resolve_llm_response_format
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
        if self.compute_config is None:
            raise OllamaClientError(
                "compute_config must be set before accessing provider"
            )

    def _build_provider(self) -> OllamaProvider:
        """Build one OllamaProvider instance from the current config."""
        from backend.llm.ollama.provider import (
            OllamaProvider,
        )

        self._validate_provider_dependencies()
        return OllamaProvider(
            base_url=self.base_url,
            dialog_recorder_base_url=self.dialog_recorder_base_url,
            primary_model=(self.profile_config.primary_model),
            dialog_recorder_model=(self.profile_config.dialog_recorder_model),
            compute_config=self.compute_config,
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
            OllamaClientError: If profile_config or
                compute_config is not set.
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

    def _build_chat_payload(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        *,
        capabilities: ModelCapabilities,
    ) -> dict[str, object]:
        """Build the Ollama chat request payload.

        Capabilities are required: it is syntactically impossible to
        construct a payload without a known context window.
        """
        payload: dict[str, object] = {
            "model": self.model,
            "messages": convert_messages_for_ollama(messages),
            "stream": True,
            "options": self._build_options(capabilities),
        }
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

    async def chat(
        self,
        messages: list[dict],
        *,
        sse_queue: asyncio.Queue | None = None,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        """Stream chat completion and return structured response.

        Resolves the model's context window via the capabilities registry
        before each call and writes the value into ``options.num_ctx`` so
        Ollama cannot truncate the prompt to its internal default.

        Args:
            messages: List of message dicts with role/content.
            sse_queue: Optional queue for real-time token
                forwarding to the Web UI via SSE.
            tools: Optional OpenAI-format tool schemas. Passed
                through to Ollama for native tool calling.

        Returns:
            LLMResponse wrapping the complete text.

        Raises:
            OllamaClientError: If chat request fails or capabilities
                cannot be resolved.
        """
        capabilities = await self._resolve_for_chat()
        payload = self._build_chat_payload(
            messages,
            tools,
            capabilities=capabilities,
        )
        try:
            full_text, tool_calls, final_usage = await self._stream_chat_chunks(
                payload,
                sse_queue,
                capabilities,
            )
        except httpx.HTTPError as e:
            raise OllamaClientError(f"Chat request failed: {e}") from e
        text = self._final_visible_text(full_text)
        raw_message = self._raw_assistant_message(text, tool_calls)
        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            raw_message=raw_message,
            usage=final_usage,
        )
