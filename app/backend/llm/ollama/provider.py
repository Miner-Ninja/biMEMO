"""Ollama LLM provider for chat completion and streaming.

Communicates with Ollama via its HTTP API using httpx.
Handles model availability checking, chat completion,
and streaming responses. Returns clear errors when
Ollama is unavailable.
"""

import json
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, ClassVar

import httpx

from backend.llm.call_factory import LLM_CALL_TIMEOUT
from backend.llm.capabilities import ContextDetectionError, ModelCapabilities
from backend.llm.capabilities_registry import CapabilitiesRegistry
from backend.llm.compute_config import ComputeConfig, ComputeMode
from backend.llm.ollama.capabilities import (
    OllamaCapabilityError,
    detect_ollama_model_capabilities,
)
from backend.llm.ollama.messages import convert_messages_for_ollama
from backend.llm.ollama.models import (
    ChatMessage,
    ChatResponse,
    ModelNotAvailableError,
    OllamaError,
)


logger = logging.getLogger(__name__)


# --- Override sources for capability resolution ---
ENV_VAR_NUM_CTX = "BIMEMO_OLLAMA_NUM_CTX"
SOURCE_ENV_OVERRIDE = "env-override"
SOURCE_UI_OVERRIDE = "ui-override"


def _read_env_override() -> int | None:
    """Read the BIMEMO_OLLAMA_NUM_CTX env var and return a positive int.

    Returns ``None`` when the variable is unset, empty, non-integer, or
    not strictly positive. Treating non-positive values as "unset" lets
    the priority chain proceed to UI override or auto-detect, per the
    spec scenario "Override value is zero or negative".
    """
    raw = os.environ.get(ENV_VAR_NUM_CTX)
    if raw is None or raw.strip() == "":
        return None
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning(
            "Invalid integer in %s=%r; ignoring override",
            ENV_VAR_NUM_CTX,
            raw,
        )
        return None
    if parsed <= 0:
        return None
    return parsed


@dataclass
class OllamaProvider:
    """Ollama LLM provider for chat completion.

    Attributes:
        base_url: Ollama API base URL for primary chat work.
        dialog_recorder_base_url: Optional Ollama API base URL
            for Dialog Recorder/Summary Distiller work.
        primary_model: Model name for chat completion.
        dialog_recorder_model: Model name for Dialog Recorder tasks.
        compute_config: GPU/CPU compute configuration.
        num_ctx: Optional explicit context window size for Ollama requests.
        timeout: Request timeout in seconds.
    """

    name: ClassVar[str] = "ollama"

    base_url: str
    primary_model: str
    dialog_recorder_model: str
    compute_config: ComputeConfig
    capabilities_registry: CapabilitiesRegistry
    dialog_recorder_base_url: str | None = None
    num_ctx: int | None = None
    timeout: float = LLM_CALL_TIMEOUT
    _client: httpx.AsyncClient = field(init=False, repr=False)
    _dialog_recorder_client: httpx.AsyncClient | None = field(
        init=False,
        repr=False,
        default=None,
    )

    def __post_init__(self) -> None:
        """Initialize the HTTP client."""
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout),
        )
        recorder_url = self.dialog_recorder_base_url
        if recorder_url and recorder_url != self.base_url:
            self._dialog_recorder_client = httpx.AsyncClient(
                base_url=self.dialog_recorder_base_url,
                timeout=httpx.Timeout(self.timeout),
            )

    def _get_client_for_model(self, model: str) -> httpx.AsyncClient:
        """Return the correct Ollama client for the target model."""
        if (
            model == self.dialog_recorder_model
            and self._dialog_recorder_client is not None
        ):
            return self._dialog_recorder_client
        return self._client

    @staticmethod
    def _ollama_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
        """Convert chat messages into the Ollama wire format."""
        return convert_messages_for_ollama(
            [{"role": message.role, "content": message.content} for message in messages]
        )

    def _chat_payload(
        self,
        *,
        target_model: str,
        messages: list[ChatMessage],
        stream: bool,
        capabilities: ModelCapabilities,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        """Build one Ollama chat request body with mandatory ``num_ctx``."""
        options = (
            self._build_chat_options(
                capabilities,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            if not stream
            else self._build_options(capabilities)
        )
        return {
            "model": target_model,
            "messages": self._ollama_messages(messages),
            "stream": stream,
            "options": options,
        }

    @staticmethod
    def _chat_response(data: dict[str, Any], *, default_model: str) -> ChatResponse:
        """Convert one Ollama response body into a chat response model."""
        return ChatResponse(
            content=data["message"]["content"],
            model=data.get("model", default_model),
            done=data.get("done", True),
        )

    @staticmethod
    def _stream_chunk_content(chunk: dict[str, Any]) -> str:
        """Extract visible assistant text from one Ollama stream chunk."""
        return str(chunk.get("message", {}).get("content", ""))

    async def _post_json(
        self,
        *,
        client: httpx.AsyncClient,
        path: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Send one JSON POST request to Ollama and return the decoded body."""
        response = await client.post(path, json=payload)
        response.raise_for_status()
        return response.json()

    async def _stream_chat_chunks(
        self,
        *,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
    ) -> AsyncIterator[str]:
        """Yield visible text chunks from one streamed Ollama chat request."""
        async with client.stream("POST", "/api/chat", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                content = self._stream_chunk_content(chunk)
                if content:
                    yield content
                if chunk.get("done", False):
                    break

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
        if self._dialog_recorder_client is not None:
            await self._dialog_recorder_client.aclose()

    async def resolve_capabilities(self, model: str) -> ModelCapabilities:
        """Resolve the available context window for ``model``.

        Runs the override-or-detect priority chain: environment variable
        override (highest), then UI override (``self.num_ctx``), then
        provider-native warmup detection. Raises ``ContextDetectionError``
        if all sources fail.

        Args:
            model: Provider-specific model identifier.

        Returns:
            A ``ModelCapabilities`` carrying the resolved context window
            and a ``source`` string identifying which step won.

        Raises:
            ContextDetectionError: If neither override nor warmup
                detection produced a positive integer.
        """
        env_override = _read_env_override()
        if env_override is not None:
            return ModelCapabilities(
                provider=self.name,
                model=model,
                max_context_tokens=env_override,
                source=SOURCE_ENV_OVERRIDE,
            )
        if self.num_ctx is not None and self.num_ctx > 0:
            return ModelCapabilities(
                provider=self.name,
                model=model,
                max_context_tokens=self.num_ctx,
                source=SOURCE_UI_OVERRIDE,
            )
        client = self._get_client_for_model(model)
        try:
            detected = await detect_ollama_model_capabilities(client, model)
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
        )

    async def check_health(self) -> bool:
        """Check if Ollama is reachable.

        Returns:
            True if Ollama responds to health check.
        """
        try:
            response = await self._client.get("/")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def list_models(
        self,
        dialog_recorder_endpoint: bool = False,
    ) -> list[str]:
        """List available models in Ollama.

        Args:
            dialog_recorder_endpoint: Whether to query the dialog recorder endpoint.

        Returns:
            List of model name strings.

        Raises:
            OllamaError: If request fails.
        """
        try:
            client = (
                self._dialog_recorder_client
                if dialog_recorder_endpoint
                else self._client
            )
            response = await (client or self._client).get("/api/tags")
            response.raise_for_status()
            data = response.json()
            return [m["name"] for m in data.get("models", [])]
        except httpx.HTTPError as e:
            raise OllamaError(f"Failed to list models: {e}") from e

    async def verify_models(self) -> None:
        """Verify required models are available in Ollama.

        Raises:
            ModelNotAvailableError: If any required model
                is not available.
            OllamaError: If Ollama is unreachable.
        """
        primary_models = await self.list_models()
        if not self._model_available(self.primary_model, primary_models):
            raise ModelNotAvailableError(
                f"Required model '{self.primary_model}' not "
                f"available in Ollama. "
                f"Available: {primary_models}"
            )
        dialog_recorder_models = await self.list_models(
            dialog_recorder_endpoint=self._dialog_recorder_client is not None
        )
        if not self._model_available(
            self.dialog_recorder_model, dialog_recorder_models
        ):
            raise ModelNotAvailableError(
                f"Required model '{self.dialog_recorder_model}' not "
                f"available in Ollama. "
                f"Available: {dialog_recorder_models}"
            )
        logger.info(
            "Verified models: primary=%s, dialog_recorder=%s,"
            " dialog_recorder_base_url=%s",
            self.primary_model,
            self.dialog_recorder_model,
            self.dialog_recorder_base_url or self.base_url,
        )

    @staticmethod
    def _model_available(required: str, available: list[str]) -> bool:
        """Check if a model name matches available models.

        Handles Ollama's naming convention where models
        may have ':latest' suffix appended.

        Args:
            required: Required model name.
            available: List of available model names.

        Returns:
            True if model is available.
        """
        for name in available:
            if name == required or name == f"{required}:latest":
                return True
        return False

    def _build_options(self, capabilities: ModelCapabilities) -> dict:
        """Build Ollama request options with mandatory ``num_ctx``.

        Args:
            capabilities: Resolved capabilities for the target model.
                ``max_context_tokens`` is always written into
                ``options.num_ctx`` so Ollama cannot truncate to its
                internal default.

        Returns:
            Options dict for the Ollama API.
        """
        options: dict = {"num_ctx": capabilities.max_context_tokens}
        if self.compute_config.mode == ComputeMode.MIXED:
            options["num_gpu"] = self.compute_config.gpu_layers
        return options

    def _build_chat_options(
        self,
        capabilities: ModelCapabilities,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> dict:
        """Build chat-specific Ollama options for background requests.

        Args:
            capabilities: Resolved capabilities for the target model.
            max_tokens: Optional hard cap for generated output tokens.
            temperature: Optional sampling temperature override.

        Returns:
            Options dict for the Ollama API.
        """
        options = self._build_options(capabilities)
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        if temperature is not None:
            options["temperature"] = temperature
        return options

    async def _resolve_for_chat(self, target_model: str) -> ModelCapabilities:
        """Resolve capabilities for ``target_model`` via the registry."""
        return await self.capabilities_registry.get_or_detect(self, target_model)

    async def chat(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        enable_thinking: bool | None = None,
    ) -> ChatResponse:
        """Send a chat completion request.

        Args:
            messages: Conversation messages.
            model: Model to use (defaults to primary_model).
            max_tokens: Optional hard cap for generated output tokens.
            temperature: Optional sampling temperature override.
            enable_thinking: Optional placeholder for provider parity.
                Ollama does not use this flag directly.

        Returns:
            Chat completion response.

        Raises:
            OllamaError: If request fails.
        """
        target_model = model or self.primary_model
        _ = enable_thinking
        capabilities = await self._resolve_for_chat(target_model)
        payload = self._chat_payload(
            target_model=target_model,
            messages=messages,
            stream=False,
            capabilities=capabilities,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        try:
            data = await self._post_json(
                client=self._get_client_for_model(target_model),
                path="/api/chat",
                payload=payload,
            )
            return self._chat_response(data, default_model=target_model)
        except httpx.HTTPError as e:
            raise OllamaError(f"Chat request failed: {e}") from e

    async def embed(
        self,
        text: str,
        model: str | None = None,
    ) -> list[float]:
        """Generate an embedding vector for text.

        Args:
            text: Input text to embed.
            model: Model to use (defaults to dialog_recorder_model).

        Returns:
            Embedding vector as list of floats.

        Raises:
            OllamaError: If request fails.
        """
        target_model = model or self.dialog_recorder_model
        payload = {"model": target_model, "input": text}
        try:
            data = await self._post_json(
                client=self._get_client_for_model(target_model),
                path="/api/embed",
                payload=payload,
            )
            return data["embeddings"][0]
        except httpx.HTTPError as e:
            raise OllamaError(f"Embed request failed: {e}") from e

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream a chat completion response.

        Args:
            messages: Conversation messages.
            model: Model to use (defaults to primary_model).

        Yields:
            Content chunks as they arrive.

        Raises:
            OllamaError: If request fails.
        """
        target_model = model or self.primary_model
        capabilities = await self._resolve_for_chat(target_model)
        payload = self._chat_payload(
            target_model=target_model,
            messages=messages,
            stream=True,
            capabilities=capabilities,
        )
        try:
            async for content in self._stream_chat_chunks(
                client=self._get_client_for_model(target_model),
                payload=payload,
            ):
                yield content
        except httpx.HTTPError as e:
            raise OllamaError(f"Stream request failed: {e}") from e
