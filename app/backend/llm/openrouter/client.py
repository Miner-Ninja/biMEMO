"""OpenRouter client for LLM access via OpenAI-compatible API.

Connects to OpenRouter at https://openrouter.ai using the
chat completions endpoint. Provides streaming chat with native
function calling and an LLM call factory for the gateway orchestrator.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

import httpx

from backend.llm.call_factory import LLMResponse, LLMToolCall
from backend.llm.capabilities import (
    ContextDetectionError,
    LlmResponseFormat,
    ModelCapabilities,
)
from backend.llm.reasoning import strip_reasoning_output
from backend.llm.response_format import resolve_llm_response_format
from backend.llm.structured_response import (
    OPENAI_STRUCTURED_RESPONSE_FORMAT,
    STRUCTURED_RESPONSE_INSTRUCTION,
    StructuredAssistantContent,
    parse_structured_response_json,
)
from backend.llm.usage import LLMCallUsage


if TYPE_CHECKING:
    import asyncio


logger = logging.getLogger(__name__)
DEFAULT_OPENROUTER_API_PREFIX = "/api/v1"
AUTHORIZATION_HEADER = "Authorization"
BEARER_PREFIX = "Bearer "
CUSTOM_API_PREFIX = "/v1"
SYSTEM_ROLE = "system"
SYSTEM_SEPARATOR = "\n\n"
ZERO_DURATION_MS = 0.0
DONE_STREAM_MARKER = "[DONE]"
EMPTY_ASSISTANT_CONTENT = ""
OPENROUTER_TOOL_CHOICE_AUTO = "auto"

# --- Capability override sources for OpenRouter ---
ENV_VAR_OPENROUTER_NUM_CTX = "BIMEMO_OPENROUTER_NUM_CTX"
ENV_VAR_CUSTOM_NUM_CTX = "BIMEMO_CUSTOM_NUM_CTX"
SOURCE_ENV_OVERRIDE = "env-override"
SOURCE_UI_OVERRIDE = "ui-override"


def _read_openrouter_env_override() -> int | None:
    """Read OpenRouter / custom env num_ctx and return a positive int.

    Treats unset, empty, non-integer, and non-positive values as "unset"
    so the priority chain proceeds to the UI override or hard error.
    Auto-detect for OpenRouter is intentionally not implemented in this
    change (deferred to a follow-up) — there is no fallback constant.
    """
    for env_name in (ENV_VAR_OPENROUTER_NUM_CTX, ENV_VAR_CUSTOM_NUM_CTX):
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


class OpenRouterClientError(Exception):
    """Raised when OpenRouter client operations fail."""


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
) -> dict:
    """Build one OpenRouter chat-completions request body."""
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": llm_response_format is LlmResponseFormat.TEXT,
    }
    if llm_response_format is LlmResponseFormat.TEXT:
        payload["stream_options"] = {"include_usage": True}
    if llm_response_format is LlmResponseFormat.JSON_SCHEMA:
        payload["response_format"] = OPENAI_STRUCTURED_RESPONSE_FORMAT
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = OPENROUTER_TOOL_CHOICE_AUTO
    return payload


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
    """Return whether one OpenRouter request should use JSON schema output."""
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
    )


def _usage_from_response_body(
    response_body: dict[str, object],
    *,
    default_model: str,
    num_ctx: int | None,
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
    )


@dataclass
class OpenRouterClient:
    """OpenRouter LLM client via OpenAI-compatible API.

    Attributes:
        api_key: OpenRouter API key.
        model: Model identifier for chat completion.
        base_url: OpenRouter API base URL (default: https://openrouter.ai).
        api_prefix: API path prefix for provider endpoints.
        num_ctx: Optional configured context window size.
        timeout: Request timeout in seconds.
    """

    name: ClassVar[str] = "openrouter"

    api_key: str
    model: str
    base_url: str = "https://openrouter.ai"
    api_prefix: str = DEFAULT_OPENROUTER_API_PREFIX
    num_ctx: int | None = None
    timeout: float = 120.0
    _client: httpx.AsyncClient = field(init=False, repr=False)

    async def resolve_capabilities(self, model: str) -> ModelCapabilities:
        """Resolve capabilities via the override-only chain.

        OpenRouter auto-detect (querying ``/api/v1/models/{slug}/endpoints``)
        is deferred to a follow-up change; for now, the resolution chain
        consists of the env override, the UI override, and a hard error.

        Args:
            model: Provider-specific model identifier.

        Returns:
            ``ModelCapabilities`` populated from the first override that
            yields a positive integer.

        Raises:
            ContextDetectionError: If neither override is set. The
                operator must configure ``BIMEMO_OPENROUTER_NUM_CTX``
                (or ``BIMEMO_CUSTOM_NUM_CTX``) or set ``num_ctx`` on
                the integration credentials until OpenRouter auto-detect
                is implemented.
        """
        env_override = _read_openrouter_env_override()
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
        raise ContextDetectionError(
            provider=self.name,
            model=model,
            attempted_sources=("env-override", "ui-override"),
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
    ) -> LLMResponse:
        """Stream chat completion and return structured response.

        Supports native OpenAI-compatible function calling via
        the ``tools`` parameter. Tool call deltas are accumulated
        across SSE chunks and returned as ``LLMToolCall`` objects.

        Args:
            messages: List of message dicts with role/content.
            sse_queue: Optional queue for real-time token
                forwarding to the Web UI via SSE.
            tools: Optional OpenAI-format tool schemas.
            tool_choice: Optional forced tool selection override.

        Returns:
            LLMResponse with text and/or tool calls.

        Raises:
            OpenRouterClientError: If chat request fails.
        """
        llm_response_format = _chat_response_format(
            provider_name=self.name,
            model_name=self.model,
        )
        request_messages = _request_messages(
            messages=messages,
            api_prefix=self.api_prefix,
            llm_response_format=llm_response_format,
        )

        payload = _chat_payload(
            model=self.model,
            messages=request_messages,
            tools=tools,
            llm_response_format=llm_response_format,
        )
        if tool_choice is not None and tools:
            payload["tool_choice"] = tool_choice

        if _uses_structured_response(llm_response_format):
            return await self._structured_chat(payload, llm_response_format)

        full_text: list[str] = []
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
                        "OpenRouter %s: %s",
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
                        num_ctx=self.num_ctx,
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
                    if reasoning_content and sse_queue is not None:
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
            logger.error("OpenRouter %s: %s", e.response.status_code, body)
            raise OpenRouterClientError(
                f"Chat request failed: {e.response.status_code} - {body}"
            ) from e
        except httpx.HTTPError as e:
            raise OpenRouterClientError(f"Chat request failed: {e}") from e

        # Build tool calls from accumulated deltas
        tool_calls: list[LLMToolCall] = []
        for tool_call_index in sorted(tool_call_acc):
            tool_call_parts = tool_call_acc[tool_call_index]
            raw_arguments = "".join(tool_call_parts["arguments_parts"])
            try:
                arguments = json.loads(raw_arguments) if raw_arguments else {}
            except json.JSONDecodeError:
                logger.warning(
                    "Failed to parse tool call arguments for %s: %s",
                    tool_call_parts["name"],
                    raw_arguments,
                )
                arguments = {}
            tool_calls.append(
                LLMToolCall(
                    id=tool_call_parts["id"],
                    name=tool_call_parts["name"],
                    arguments=arguments,
                )
            )

        text = "".join(full_text)
        if self.api_prefix == CUSTOM_API_PREFIX:
            # Defensive cleanup: in the rare case where the upstream provider
            # leaks a raw ``<think>...</think>`` block into the visible channel
            # (no server-side reasoning parser configured), strip it from the
            # text saved to conversation history.
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
    ) -> StructuredAssistantContent | None:
        """Build one structured assistant response from a non-streaming reply."""
        tool_calls = self._tool_calls_from_message(message)
        if tool_calls:
            return None
        thinking = self._thinking_from_message(message)
        return parse_structured_response_json(
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
                        raw_tool_call.get("id", f"openrouter-call-{tool_call_index}")
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
        if not isinstance(raw_arguments, str) or not raw_arguments.strip():
            return {}
        try:
            parsed_arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            logger.warning(
                "Failed to parse OpenRouter tool call arguments: %s",
                raw_arguments,
            )
            return {}
        if isinstance(parsed_arguments, dict):
            return parsed_arguments
        return {}

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
    ) -> LLMResponse:
        """Request one non-streaming structured OpenRouter response."""
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
            logger.error("OpenRouter %s: %s", error.response.status_code, body)
            raise OpenRouterClientError(
                f"Chat request failed: {error.response.status_code} - {body}"
            ) from error
        except httpx.HTTPError as error:
            raise OpenRouterClientError(f"Chat request failed: {error}") from error

        response_body = response.json()
        choices = response_body.get("choices", [])
        if not isinstance(choices, list) or not choices:
            raise OpenRouterClientError("Structured chat response is missing choices")
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise OpenRouterClientError("Structured chat response has invalid choices")
        message = first_choice.get("message", {})
        if not isinstance(message, dict):
            raise OpenRouterClientError("Structured chat response is missing a message")
        tool_calls = self._tool_calls_from_message(message)
        structured_content = self._structured_content_from_message(message)
        text = structured_content.answer if structured_content is not None else ""
        return LLMResponse(
            text=text,
            tool_calls=tool_calls,
            raw_message=self._raw_message_from_response(
                text=text,
                tool_calls=tool_calls,
            ),
            usage=_usage_from_response_body(
                response_body,
                default_model=self.model,
                num_ctx=self.num_ctx,
            ),
            llm_response_format=llm_response_format,
            structured_content=structured_content,
        )
