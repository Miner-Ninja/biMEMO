"""OpenRouter LLM provider for chat completion and streaming.

Communicates with OpenRouter via OpenAI-compatible API using httpx.
Provides chat completion for Dialog Recorder/Summary Distiller background tasks.
Embeddings are not supported — returns empty vectors.
"""

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx

from backend.llm.ollama.models import ChatMessage, ChatResponse
from backend.llm.openrouter.client import (
    CUSTOM_API_PREFIX,
    _build_auth_headers,
    normalize_system_messages,
)
from backend.llm.reasoning import strip_reasoning_output


logger = logging.getLogger(__name__)
DEFAULT_OPENROUTER_API_PREFIX = "/api/v1"
DONE_STREAM_MARKER = "[DONE]"


class OpenRouterError(Exception):
    """Raised when OpenRouter communication fails."""


@dataclass
class OpenRouterProvider:
    """OpenRouter LLM provider for background tasks.

    Attributes:
        api_key: OpenRouter API key.
        primary_model: Model name for chat completion.
        dialog_recorder_model: Model name for Dialog Recorder tasks.
        base_url: OpenRouter API base URL for primary tasks.
        dialog_recorder_base_url: Optional base URL for dialog recorder tasks.
        embedding_base_url: Optional base URL for the embedding endpoint.
            When set, embed() targets this URL instead of base_url so the
            embedding service can run as a separate container.
        api_prefix: API path prefix for provider endpoints.
        timeout: Request timeout in seconds.
    """

    api_key: str
    primary_model: str
    dialog_recorder_model: str
    base_url: str = "https://openrouter.ai"
    dialog_recorder_base_url: str | None = None
    embedding_base_url: str | None = None
    api_prefix: str = DEFAULT_OPENROUTER_API_PREFIX
    embedding_model: str = ""
    timeout: float = 120.0
    _client: httpx.AsyncClient = field(init=False, repr=False)
    _dialog_recorder_client: httpx.AsyncClient | None = field(
        init=False,
        repr=False,
        default=None,
    )
    _embedding_client: httpx.AsyncClient | None = field(
        init=False,
        repr=False,
        default=None,
    )

    def __post_init__(self) -> None:
        """Initialize the HTTP client."""
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout),
            headers=_build_auth_headers(self.api_key),
        )
        recorder_url = self.dialog_recorder_base_url
        if recorder_url and recorder_url != self.base_url:
            self._dialog_recorder_client = httpx.AsyncClient(
                base_url=self.dialog_recorder_base_url,
                timeout=httpx.Timeout(self.timeout),
                headers=_build_auth_headers(self.api_key),
            )
        embedding_url = self.embedding_base_url
        if embedding_url and embedding_url != self.base_url:
            self._embedding_client = httpx.AsyncClient(
                base_url=self.embedding_base_url,
                timeout=httpx.Timeout(self.timeout),
                headers=_build_auth_headers(self.api_key),
            )

    def _get_client_for_model(self, model: str) -> httpx.AsyncClient:
        """Return the correct client for the target model."""
        if (
            model == self.dialog_recorder_model
            and self._dialog_recorder_client is not None
        ):
            return self._dialog_recorder_client
        return self._client

    def _request_messages(self, messages: list[ChatMessage]) -> list[dict]:
        """Convert chat messages into the provider wire format."""
        request_messages = [
            {
                "role": message.role,
                "content": message.content,
            }
            for message in messages
        ]
        if self.api_prefix == CUSTOM_API_PREFIX:
            return normalize_system_messages(request_messages)
        return request_messages

    def _chat_payload(
        self,
        *,
        target_model: str,
        messages: list[ChatMessage],
        stream: bool,
        max_tokens: int | None = None,
        temperature: float | None = None,
        enable_thinking: bool | None = None,
    ) -> dict:
        """Build one OpenRouter chat request body."""
        payload: dict = {
            "model": target_model,
            "messages": self._request_messages(messages),
            "stream": stream,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature
        if enable_thinking is not None and self.api_prefix == CUSTOM_API_PREFIX:
            payload["chat_template_kwargs"] = {
                "enable_thinking": enable_thinking,
            }
        return payload

    def _visible_content(self, content: str) -> str:
        """Return assistant text after provider-specific reasoning stripping."""
        if self.api_prefix == CUSTOM_API_PREFIX:
            return strip_reasoning_output(content)
        return content

    async def _post_json(
        self,
        *,
        client: httpx.AsyncClient,
        path: str,
        payload: dict,
    ) -> dict:
        """Send one JSON POST request and return the decoded response body."""
        response = await client.post(path, json=payload)
        response.raise_for_status()
        return response.json()

    def _chat_response(
        self,
        data: dict,
        *,
        target_model: str,
    ) -> ChatResponse:
        """Build one provider chat response from the decoded API body."""
        choices = data.get("choices", [])
        content = ""
        if choices:
            content = choices[0].get("message", {}).get("content", "")
        content = self._visible_content(content)
        return ChatResponse(
            content=content,
            model=data.get("model", target_model),
            done=True,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
        if self._dialog_recorder_client is not None:
            await self._dialog_recorder_client.aclose()
        if self._embedding_client is not None:
            await self._embedding_client.aclose()

    async def check_health(self) -> bool:
        """Check if OpenRouter is reachable.

        Returns:
            True if OpenRouter responds.
        """
        try:
            response = await self._client.get(f"{self.api_prefix}/models")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def verify_models(self) -> None:
        """Verify models are available (no-op for OpenRouter).

        OpenRouter models are always available if the API key
        is valid. Logs info and returns.
        """
        logger.info(
            "OpenRouter provider: models assumed available"
            " (primary=%s, dialog_recorder=%s)",
            self.primary_model,
            self.dialog_recorder_model,
        )

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
            enable_thinking: Optional override for models that expose a
                request-level thinking switch (used by custom/vLLM).

        Returns:
            Chat completion response.

        Raises:
            OpenRouterError: If request fails.
        """
        target_model = model or self.primary_model
        payload = self._chat_payload(
            target_model=target_model,
            messages=messages,
            stream=False,
            max_tokens=max_tokens,
            temperature=temperature,
            enable_thinking=enable_thinking,
        )
        try:
            data = await self._post_json(
                client=self._get_client_for_model(target_model),
                path=f"{self.api_prefix}/chat/completions",
                payload=payload,
            )
            return self._chat_response(data, target_model=target_model)
        except httpx.HTTPError as e:
            raise OpenRouterError(f"Chat request failed: {e}") from e

    async def embed(
        self,
        text: str,
        model: str | None = None,
    ) -> list[float]:
        """Generate an embedding vector via OpenRouter-compatible API.

        Uses the ``embedding_model`` field if set, otherwise the
        explicit ``model`` parameter. Returns an empty list if no
        embedding model is configured (graceful degradation).

        Args:
            text: Input text to embed.
            model: Override embedding model name.

        Returns:
            Embedding vector as list of floats, or empty list.
        """
        target_model = model or self.embedding_model
        if not target_model:
            return []
        payload = {
            "model": target_model,
            "input": text,
        }
        embedding_client = self._embedding_client or self._client
        try:
            data = await self._post_json(
                client=embedding_client,
                path=f"{self.api_prefix}/embeddings",
                payload=payload,
            )
            embeddings = data.get("data", [])
            if embeddings:
                return embeddings[0].get("embedding", [])
            return []
        except httpx.HTTPError:
            logger.warning("Embedding request failed", exc_info=True)
            return []

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
            OpenRouterError: If request fails.
        """
        target_model = model or self.primary_model
        payload = self._chat_payload(
            target_model=target_model,
            messages=messages,
            stream=True,
        )
        try:
            async with self._get_client_for_model(target_model).stream(
                "POST",
                f"{self.api_prefix}/chat/completions",
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == DONE_STREAM_MARKER:
                        break
                    chunk = json.loads(data_str)
                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        # The inference server splits visible content from
                        # reasoning content via its own reasoning parser, so
                        # we forward each chunk immediately without buffering
                        # or client-side ``<think>`` parsing.
                        yield content
        except httpx.HTTPError as e:
            raise OpenRouterError(f"Stream request failed: {e}") from e
