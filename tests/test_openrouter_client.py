"""Tests for OpenRouterClient SSE streaming and LLM call factory.

Covers SSE parsing, token forwarding, close, and make_llm_call.
Uses httpx MockTransport to avoid real network calls.
"""

import asyncio
import json

import httpx
import pytest

from backend.llm.call_factory import LLM_CALL_TIMEOUT, LLMResponse, make_llm_call
from backend.llm.capabilities import LlmResponseFormat
from backend.llm.custom_provider.client import SOURCE_CUSTOM_MODELS, CustomLLMClient
from backend.llm.openrouter.client import (
    DEFAULT_OPENAI_COMPAT_CONTEXT_TOKENS,
    SOURCE_PROVIDER_DEFAULT,
    OpenRouterClient,
    OpenRouterClientError,
)
from backend.llm.reasoning import strip_reasoning_output
from backend.llm.response_format import EXACT_RESPONSE_FORMAT_OVERRIDES


# --- Test fixtures ---
DEFAULT_OPENROUTER_MODEL = "qwen/qwen3-14b"
DEFAULT_CUSTOM_MODEL = "Qwen/Qwen3.5-9B"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai"
DEFAULT_CUSTOM_BASE_URL = "http://vllm-primary:8000"
OPENROUTER_ENV_OVERRIDE = "BIMEMO_OPENROUTER_NUM_CTX"
OPENROUTER_ENV_OVERRIDE_VALUE = "32000"
OPENROUTER_ENV_OVERRIDE_TOKENS = 32000
STRUCTURED_OPENROUTER_MODEL = "openai/gpt-4o"
STRUCTURED_OPENROUTER_OVERRIDE_KEY = ("openrouter", STRUCTURED_OPENROUTER_MODEL)
STRUCTURED_CUSTOM_OVERRIDE_KEY = ("custom_llm", STRUCTURED_OPENROUTER_MODEL)
STRUCTURED_ANSWER_TEXT = "Structured answer"
STRUCTURED_SQL_TEXT = "select 1"
STRUCTURED_SUGGESTION_TEXT = "Follow up"
STRUCTURED_PROMPT_TOKENS = 21
STRUCTURED_COMPLETION_TOKENS = 9


def make_client(transport, model=DEFAULT_OPENROUTER_MODEL):
    """Create a client with mock transport."""
    client = OpenRouterClient(
        api_key="test-api-key",
        model=model,
    )
    client._client = httpx.AsyncClient(
        transport=transport,
        base_url=DEFAULT_OPENROUTER_BASE_URL,
        headers={"Authorization": "Bearer test-api-key"},
    )
    return client


def make_custom_client(transport, model=DEFAULT_CUSTOM_MODEL, num_ctx=None):
    """Create a custom-provider client with OpenAI-style /v1 paths."""
    client = CustomLLMClient(
        api_key="",
        model=model,
        base_url=DEFAULT_CUSTOM_BASE_URL,
        api_prefix="/v1",
        num_ctx=num_ctx,
    )
    client._client = httpx.AsyncClient(
        transport=transport,
        base_url=DEFAULT_CUSTOM_BASE_URL,
        headers={},
    )
    return client


class TestOpenRouterClientInit:
    """Tests for OpenRouterClient initialization."""

    def test_sets_bearer_header(self):
        """Verify Authorization header is set."""
        client = OpenRouterClient(
            api_key="my-key",
            model=DEFAULT_OPENROUTER_MODEL,
        )
        auth = client._client.headers["authorization"]
        assert auth == "Bearer my-key"

    def test_sets_base_url(self):
        """Verify base URL points to OpenRouter."""
        client = OpenRouterClient(
            api_key="my-key",
            model=DEFAULT_OPENROUTER_MODEL,
        )
        assert str(client._client.base_url) == DEFAULT_OPENROUTER_BASE_URL

    def test_omits_auth_header_when_api_key_is_empty(self):
        """Verify empty API keys do not create an invalid Bearer header."""
        client = OpenRouterClient(
            api_key="",
            model=DEFAULT_CUSTOM_MODEL,
            base_url=DEFAULT_CUSTOM_BASE_URL,
            api_prefix="/v1",
        )
        assert "authorization" not in client._client.headers

    def test_strip_reasoning_output_keeps_only_visible_text(self):
        """Verify leaked reasoning text is removed from custom model output."""
        assert (
            strip_reasoning_output("Internal reasoning\n</think>\n\nVisible answer")
            == "Visible answer"
        )


class TestChat:
    """Tests for OpenRouter chat streaming."""

    @pytest.mark.asyncio
    async def test_returns_complete_text(self):
        """Verify SSE streaming returns full text."""
        lines = [
            'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            'data: {"choices":[{"delta":{"content":" world"}}]}',
            "data: [DONE]",
        ]

        async def handler(request):
            body = json.loads(request.content)
            assert body["model"] == DEFAULT_OPENROUTER_MODEL
            assert body["stream"] is True
            return httpx.Response(200, text="\n".join(lines))

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        result = await client.chat([{"role": "user", "content": "Hi"}])
        assert isinstance(result, LLMResponse)
        assert result.text == "Hello world"
        assert result.llm_response_format is LlmResponseFormat.TEXT
        assert result.tool_calls == []
        await client.close()

    @pytest.mark.asyncio
    async def test_custom_provider_merges_system_messages(self):
        """Verify custom/vLLM sends a single leading system message."""
        lines = [
            'data: {"choices":[{"delta":{"content":"ok"}}]}',
            "data: [DONE]",
        ]

        async def handler(request):
            body = json.loads(request.content)
            assert str(request.url).endswith("/v1/chat/completions")
            assert body["messages"] == [
                {
                    "role": "system",
                    "content": "System A\n\nSystem B",
                },
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello"},
            ]
            return httpx.Response(200, text="\n".join(lines))

        transport = httpx.MockTransport(handler)
        client = make_custom_client(transport)
        result = await client.chat(
            [
                {"role": "system", "content": "System A"},
                {"role": "user", "content": "Hi"},
                {"role": "system", "content": "System B"},
                {"role": "assistant", "content": "Hello"},
            ]
        )
        assert result.text == "ok"
        await client.close()

    @pytest.mark.asyncio
    async def test_custom_provider_forwards_reasoning_content_channel(self):
        """Verify ``delta.reasoning_content`` is forwarded as a thinking event.

        The custom provider relies on the inference server's reasoning parser
        to split reasoning from visible text. Each chunk is forwarded
        immediately without client-side buffering or ``<think>`` parsing.
        """
        lines = [
            ('data: {"choices":[{"delta":{"reasoning_content":"internal plan"}}]}'),
            'data: {"choices":[{"delta":{"content":"Visible"}}]}',
            "data: [DONE]",
        ]

        async def handler(request):
            return httpx.Response(200, text="\n".join(lines))

        transport = httpx.MockTransport(handler)
        client = make_custom_client(transport)
        queue: asyncio.Queue = asyncio.Queue()
        result = await client.chat(
            [{"role": "user", "content": "Hi"}],
            stream_queue=queue,
        )
        assert result.text == "Visible"
        queued = [queue.get_nowait() for _ in range(queue.qsize())]
        assert {"type": "thinking", "content": "internal plan"} in queued
        assert {"type": "token", "content": "Visible"} in queued
        assert {"type": "token", "content": "internal plan"} not in queued
        await client.close()

    @pytest.mark.asyncio
    async def test_custom_provider_strips_leaked_reasoning_from_final_text(self):
        """Verify a leaked ``<think>`` block is stripped from saved text.

        Defensive cleanup: when an upstream provider has no server-side
        reasoning parser configured, the visible channel may include a raw
        ``<think>...</think>`` block. The client must strip it before
        returning the assembled text for conversation history storage.
        """
        lines = [
            'data: {"choices":[{"delta":{"content":"Reasoning line"}}]}',
            'data: {"choices":[{"delta":{"content":"</think>\\n\\nVisible"}}]}',
            "data: [DONE]",
        ]

        async def handler(request):
            return httpx.Response(200, text="\n".join(lines))

        transport = httpx.MockTransport(handler)
        client = make_custom_client(transport)
        result = await client.chat([{"role": "user", "content": "Hi"}])
        assert result.text == "Visible"
        await client.close()

    @pytest.mark.asyncio
    async def test_forwards_to_stream_queue(self):
        """Verify tokens are placed on SSE queue as separate events.

        Each streamed chunk is forwarded immediately without buffering, so
        the queue receives one event per chunk in arrival order.
        """
        lines = [
            'data: {"choices":[{"delta":{"content":"tok1"}}]}',
            'data: {"choices":[{"delta":{"content":"tok2"}}]}',
            "data: [DONE]",
        ]

        async def handler(request):
            return httpx.Response(200, text="\n".join(lines))

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        queue: asyncio.Queue = asyncio.Queue()

        result = await client.chat(
            [{"role": "user", "content": "Hi"}],
            stream_queue=queue,
        )

        assert result.text == "tok1tok2"
        assert queue.qsize() == 2
        assert queue.get_nowait() == {"type": "token", "content": "tok1"}
        assert queue.get_nowait() == {"type": "token", "content": "tok2"}
        await client.close()

    @pytest.mark.asyncio
    async def test_no_queue_no_error(self):
        """Verify chat works without SSE queue."""
        lines = [
            'data: {"choices":[{"delta":{"content":"ok"}}]}',
            "data: [DONE]",
        ]

        async def handler(request):
            return httpx.Response(200, text="\n".join(lines))

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        result = await client.chat([{"role": "user", "content": "Hi"}])
        assert result.text == "ok"
        await client.close()

    @pytest.mark.asyncio
    async def test_empty_content_skipped(self):
        """Verify chunks with empty content are skipped."""
        lines = [
            'data: {"choices":[{"delta":{"content":""}}]}',
            'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            "data: [DONE]",
        ]

        async def handler(request):
            return httpx.Response(200, text="\n".join(lines))

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        result = await client.chat([{"role": "user", "content": "Hi"}])
        assert result.text == "Hello"
        await client.close()

    @pytest.mark.asyncio
    async def test_non_data_lines_skipped(self):
        """Verify non-data lines are ignored."""
        lines = [
            ": comment",
            'data: {"choices":[{"delta":{"content":"Hi"}}]}',
            "",
            "data: [DONE]",
        ]

        async def handler(request):
            return httpx.Response(200, text="\n".join(lines))

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        result = await client.chat([{"role": "user", "content": "test"}])
        assert result.text == "Hi"
        await client.close()

    @pytest.mark.asyncio
    async def test_empty_choices_skipped(self):
        """Verify chunks with empty choices are skipped."""
        lines = [
            'data: {"choices":[]}',
            'data: {"choices":[{"delta":{"content":"ok"}}]}',
            "data: [DONE]",
        ]

        async def handler(request):
            return httpx.Response(200, text="\n".join(lines))

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        result = await client.chat([{"role": "user", "content": "Hi"}])
        assert result.text == "ok"
        await client.close()

    @pytest.mark.asyncio
    async def test_connection_error(self):
        """Verify raises OpenRouterClientError on failure."""

        async def handler(request):
            raise httpx.ConnectError("refused")

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        with pytest.raises(OpenRouterClientError, match="Chat request"):
            await client.chat([{"role": "user", "content": "Hi"}])
        await client.close()

    @pytest.mark.asyncio
    async def test_server_error(self):
        """Verify raises OpenRouterClientError on 500."""

        async def handler(request):
            return httpx.Response(500, text="error")

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        with pytest.raises(OpenRouterClientError, match="Chat request"):
            await client.chat([{"role": "user", "content": "Hi"}])
        await client.close()

    @pytest.mark.asyncio
    async def test_custom_provider_uses_v1_prefix(self):
        """Verify custom-provider chat uses OpenAI-style /v1 path."""
        lines = [
            'data: {"choices":[{"delta":{"content":"ok"}}]}',
            "data: [DONE]",
        ]

        async def handler(request):
            assert str(request.url).endswith("/v1/chat/completions")
            return httpx.Response(200, text="\n".join(lines))

        transport = httpx.MockTransport(handler)
        client = make_custom_client(transport)
        result = await client.chat([{"role": "user", "content": "Hi"}])
        assert result.text == "ok"
        await client.close()

    @pytest.mark.asyncio
    async def test_custom_provider_collects_usage_from_final_chunk(self):
        """Verify usage survives a final OpenAI-style usage-only chunk."""
        lines = [
            'data: {"choices":[{"delta":{"content":"ok"}}],"model":"qwen3.5:35b-4bit"}',
            (
                'data: {"choices":[],"usage":{"prompt_tokens":321,'
                '"completion_tokens":54},"model":"qwen3.5:35b-4bit"}'
            ),
            "data: [DONE]",
        ]

        async def handler(request):
            return httpx.Response(200, text="\n".join(lines))

        transport = httpx.MockTransport(handler)
        client = make_custom_client(transport)
        result = await client.chat([{"role": "user", "content": "Hi"}])
        assert result.text == "ok"
        assert result.usage is not None
        assert result.usage.prompt_tokens == 321
        assert result.usage.completion_tokens == 54
        assert result.usage.model == "qwen3.5:35b-4bit"
        await client.close()

    @pytest.mark.asyncio
    async def test_custom_provider_usage_infers_context_from_num_ctx(self):
        """Verify configured num_ctx is exposed as context usage for custom LLMs."""
        lines = [
            'data: {"choices":[{"delta":{"content":"ok"}}],"model":"qwen3.5:35b-4bit"}',
            (
                'data: {"choices":[],"usage":{"prompt_tokens":321,'
                '"completion_tokens":54},"model":"qwen3.5:35b-4bit"}'
            ),
            "data: [DONE]",
        ]

        async def handler(request):
            return httpx.Response(200, text="\n".join(lines))

        transport = httpx.MockTransport(handler)
        client = make_custom_client(transport, num_ctx=32768)
        result = await client.chat([{"role": "user", "content": "Hi"}])
        assert result.usage is not None
        assert result.usage.context_total == 32768
        assert result.usage.context_used == 375
        await client.close()

    @pytest.mark.asyncio
    async def test_structured_response_uses_non_streaming_json_schema(self):
        """Verify one exact override enables the structured JSON path."""

        async def handler(request):
            body = json.loads(request.content)
            assert body["stream"] is False
            assert "stream_options" not in body
            assert body["response_format"]["type"] == "json_schema"
            assert body["messages"][0]["role"] == "system"
            assert "JSON object" in body["messages"][0]["content"]
            response_body = {
                "model": STRUCTURED_OPENROUTER_MODEL,
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "answer": STRUCTURED_ANSWER_TEXT,
                                    "sql": STRUCTURED_SQL_TEXT,
                                    "suggestions": [STRUCTURED_SUGGESTION_TEXT],
                                }
                            ),
                            "reasoning": "Internal plan",
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": STRUCTURED_PROMPT_TOKENS,
                    "completion_tokens": STRUCTURED_COMPLETION_TOKENS,
                },
            }
            return httpx.Response(200, json=response_body)

        EXACT_RESPONSE_FORMAT_OVERRIDES[STRUCTURED_OPENROUTER_OVERRIDE_KEY] = (
            LlmResponseFormat.JSON_SCHEMA
        )
        transport = httpx.MockTransport(handler)
        client = make_client(transport, model=STRUCTURED_OPENROUTER_MODEL)
        try:
            result = await client.chat([{"role": "user", "content": "Hi"}])
        finally:
            del EXACT_RESPONSE_FORMAT_OVERRIDES[STRUCTURED_OPENROUTER_OVERRIDE_KEY]
            await client.close()
        assert result.text == STRUCTURED_ANSWER_TEXT
        assert result.llm_response_format is LlmResponseFormat.JSON_SCHEMA
        assert result.structured_content is not None
        assert result.structured_content.answer == STRUCTURED_ANSWER_TEXT
        assert result.structured_content.suggestions == [STRUCTURED_SUGGESTION_TEXT]
        assert result.structured_content.thinking == "Internal plan"
        assert result.usage is not None
        assert result.usage.prompt_tokens == STRUCTURED_PROMPT_TOKENS
        assert result.usage.completion_tokens == STRUCTURED_COMPLETION_TOKENS

    @pytest.mark.asyncio
    async def test_custom_provider_merges_instruction_into_one_system_message(self):
        """Keep one leading system message for custom structured providers."""

        async def handler(request):
            body = json.loads(request.content)
            assert body["stream"] is False
            assert len(body["messages"]) == 2
            assert body["messages"][0]["role"] == "system"
            assert body["messages"][1] == {"role": "user", "content": "Hi"}
            assert "System A" in body["messages"][0]["content"]
            assert "System B" in body["messages"][0]["content"]
            assert "JSON object" in body["messages"][0]["content"]
            response_body = {
                "model": STRUCTURED_OPENROUTER_MODEL,
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "answer": STRUCTURED_ANSWER_TEXT,
                                    "sql": None,
                                    "suggestions": [],
                                }
                            ),
                        }
                    }
                ],
            }
            return httpx.Response(200, json=response_body)

        EXACT_RESPONSE_FORMAT_OVERRIDES[STRUCTURED_CUSTOM_OVERRIDE_KEY] = (
            LlmResponseFormat.JSON_SCHEMA
        )
        transport = httpx.MockTransport(handler)
        client = make_custom_client(transport, model=STRUCTURED_OPENROUTER_MODEL)
        try:
            result = await client.chat(
                [
                    {"role": "system", "content": "System A"},
                    {"role": "user", "content": "Hi"},
                    {"role": "system", "content": "System B"},
                ]
            )
        finally:
            del EXACT_RESPONSE_FORMAT_OVERRIDES[STRUCTURED_CUSTOM_OVERRIDE_KEY]
            await client.close()
        assert result.llm_response_format is LlmResponseFormat.JSON_SCHEMA
        assert result.text == STRUCTURED_ANSWER_TEXT


class TestClose:
    """Tests for close method."""

    @pytest.mark.asyncio
    async def test_close_works(self):
        """Verify close does not raise."""
        client = OpenRouterClient(
            api_key="test-key",
            model="qwen/qwen3-14b",
        )
        await client.close()


class TestResolveCapabilities:
    """Tests for OpenRouter capability resolution."""

    @pytest.mark.asyncio
    async def test_env_override_returns_text_response_format(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Keep OpenRouter on text mode until structured output is proven."""
        monkeypatch.setenv(OPENROUTER_ENV_OVERRIDE, OPENROUTER_ENV_OVERRIDE_VALUE)
        client = OpenRouterClient(
            api_key="test-api-key",
            model=DEFAULT_OPENROUTER_MODEL,
        )
        try:
            caps = await client.resolve_capabilities(DEFAULT_OPENROUTER_MODEL)
        finally:
            await client.close()
        assert caps.max_context_tokens == OPENROUTER_ENV_OVERRIDE_TOKENS
        assert caps.llm_response_format is LlmResponseFormat.TEXT

    @pytest.mark.asyncio
    async def test_missing_override_uses_conservative_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Use a safe provider default when context overrides are absent."""
        monkeypatch.delenv(OPENROUTER_ENV_OVERRIDE, raising=False)
        monkeypatch.delenv("BIMEMO_CUSTOM_NUM_CTX", raising=False)
        transport = httpx.MockTransport(lambda request: httpx.Response(404))
        client = OpenRouterClient(
            api_key="test-api-key",
            model=DEFAULT_CUSTOM_MODEL,
            api_prefix="/v1",
        )
        client._client = httpx.AsyncClient(
            transport=transport,
            base_url=DEFAULT_CUSTOM_BASE_URL,
        )
        try:
            caps = await client.resolve_capabilities(DEFAULT_CUSTOM_MODEL)
        finally:
            await client.close()
        assert caps.max_context_tokens == DEFAULT_OPENAI_COMPAT_CONTEXT_TOKENS
        assert caps.source == SOURCE_PROVIDER_DEFAULT

    @pytest.mark.asyncio
    async def test_custom_provider_detects_context_from_models_metadata(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Detect custom provider context from OpenAI-compatible model metadata."""
        monkeypatch.delenv(OPENROUTER_ENV_OVERRIDE, raising=False)
        monkeypatch.delenv("BIMEMO_CUSTOM_NUM_CTX", raising=False)

        async def handler(request):
            assert str(request.url).endswith("/v1/models")
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": DEFAULT_CUSTOM_MODEL,
                            "max_model_len": 32768,
                        }
                    ]
                },
            )

        transport = httpx.MockTransport(handler)
        client = make_custom_client(transport)
        try:
            caps = await client.resolve_capabilities(DEFAULT_CUSTOM_MODEL)
        finally:
            await client.close()
        assert caps.max_context_tokens == 32768
        assert caps.source == SOURCE_CUSTOM_MODELS
        assert caps.provider == "custom_llm"

    @pytest.mark.asyncio
    async def test_detected_context_is_used_for_usage_accounting(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Use detected context window in usage when num_ctx is not explicit."""
        monkeypatch.delenv(OPENROUTER_ENV_OVERRIDE, raising=False)
        monkeypatch.delenv("BIMEMO_CUSTOM_NUM_CTX", raising=False)
        lines = [
            'data: {"choices":[{"delta":{"content":"ok"}}],"model":"qwen3.5:35b-4bit"}',
            (
                'data: {"choices":[],"usage":{"prompt_tokens":321,'
                '"completion_tokens":54},"model":"qwen3.5:35b-4bit"}'
            ),
            "data: [DONE]",
        ]

        async def handler(request):
            if str(request.url).endswith("/v1/models"):
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": DEFAULT_CUSTOM_MODEL,
                                "max_context_tokens": 32768,
                            }
                        ]
                    },
                )
            return httpx.Response(200, text="\n".join(lines))

        transport = httpx.MockTransport(handler)
        client = make_custom_client(transport)
        try:
            await client.resolve_capabilities(DEFAULT_CUSTOM_MODEL)
            result = await client.chat([{"role": "user", "content": "Hi"}])
        finally:
            await client.close()
        assert result.usage is not None
        assert result.usage.context_total == 32768
        assert result.usage.context_used == 375


class TestMakeLlmCall:
    """Tests for make_llm_call factory."""

    @pytest.mark.asyncio
    async def test_returns_callable(self):
        """Verify factory returns an async callable."""
        lines = [
            'data: {"choices":[{"delta":{"content":"response"}}]}',
            "data: [DONE]",
        ]

        async def handler(request):
            return httpx.Response(200, text="\n".join(lines))

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        llm_call = make_llm_call(client)
        result = await llm_call([{"role": "user", "content": "Hi"}])
        assert isinstance(result, LLMResponse)
        assert result.text == "response"
        await client.close()

    @pytest.mark.asyncio
    async def test_forwards_to_queue(self):
        """Verify llm_call forwards tokens to queue."""
        lines = [
            'data: {"choices":[{"delta":{"content":"tok"}}]}',
            "data: [DONE]",
        ]

        async def handler(request):
            return httpx.Response(200, text="\n".join(lines))

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        queue: asyncio.Queue = asyncio.Queue()
        llm_call = make_llm_call(client, stream_queue=queue)
        await llm_call([{"role": "user", "content": "Hi"}])
        assert queue.qsize() == 1
        await client.close()

    def test_timeout_constant(self):
        """Verify LLM call timeout is bounded for chat responsiveness."""
        assert LLM_CALL_TIMEOUT == 120.0


# --- Per-call LLMCallOptions overrides ---


class TestOpenRouterClientOptions:
    """Tests for per-call LLMCallOptions overrides on OpenRouter chat."""

    @pytest.mark.asyncio
    async def test_text_override_skips_response_format_field(self):
        """Verify TEXT override omits response_format from the payload."""
        from backend.llm.call_options import LLMCallOptions

        received: dict = {}

        async def handler(request):
            body = json.loads(request.content)
            received["body"] = body
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(
                    b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                    b"data: [DONE]\n\n"
                ),
            )

        EXACT_RESPONSE_FORMAT_OVERRIDES[STRUCTURED_OPENROUTER_OVERRIDE_KEY] = (
            LlmResponseFormat.JSON_SCHEMA
        )
        transport = httpx.MockTransport(handler)
        client = make_client(transport, model=STRUCTURED_OPENROUTER_MODEL)
        try:
            await client.chat(
                [{"role": "user", "content": "hi"}],
                options=LLMCallOptions(response_format=LlmResponseFormat.TEXT),
            )
        finally:
            del EXACT_RESPONSE_FORMAT_OVERRIDES[STRUCTURED_OPENROUTER_OVERRIDE_KEY]
            await client.close()
        assert "response_format" not in received["body"]
        assert not any(
            msg.get("role") == "system"
            and "requested schema" in (msg.get("content", ""))
            for msg in received["body"]["messages"]
        )

    @pytest.mark.asyncio
    async def test_schema_override_replaces_default_response_format(self):
        """Verify a custom schema replaces the default response_format block."""
        from backend.llm.call_options import LLMCallOptions
        from backend.llm.structured_response import SKILL_EXTRACTION_SCHEMA

        received: dict = {}

        async def handler(request):
            body = json.loads(request.content)
            received["body"] = body
            response_body = {
                "model": STRUCTURED_OPENROUTER_MODEL,
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "{}",
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                },
            }
            return httpx.Response(200, json=response_body)

        EXACT_RESPONSE_FORMAT_OVERRIDES[STRUCTURED_OPENROUTER_OVERRIDE_KEY] = (
            LlmResponseFormat.JSON_SCHEMA
        )
        transport = httpx.MockTransport(handler)
        client = make_client(transport, model=STRUCTURED_OPENROUTER_MODEL)
        try:
            await client.chat(
                [{"role": "user", "content": "hi"}],
                options=LLMCallOptions(
                    response_format=LlmResponseFormat.JSON_SCHEMA,
                    response_schema=SKILL_EXTRACTION_SCHEMA,
                ),
            )
        finally:
            del EXACT_RESPONSE_FORMAT_OVERRIDES[STRUCTURED_OPENROUTER_OVERRIDE_KEY]
            await client.close()
        response_format = received["body"]["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["schema"] == SKILL_EXTRACTION_SCHEMA
        assert response_format["json_schema"]["strict"] is True

    @pytest.mark.asyncio
    async def test_schema_override_returns_raw_text(self):
        """Verify custom-schema responses land on text, not structured_content."""
        from backend.llm.call_options import LLMCallOptions
        from backend.llm.structured_response import SKILL_EXTRACTION_SCHEMA

        raw_json = (
            '{"title":"x","description":"d","trigger":"t","procedure":[],'
            '"errors":[],"verification":"v"}'
        )

        async def handler(request):
            return httpx.Response(
                200,
                json={
                    "model": STRUCTURED_OPENROUTER_MODEL,
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": raw_json,
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )

        EXACT_RESPONSE_FORMAT_OVERRIDES[STRUCTURED_OPENROUTER_OVERRIDE_KEY] = (
            LlmResponseFormat.JSON_SCHEMA
        )
        transport = httpx.MockTransport(handler)
        client = make_client(transport, model=STRUCTURED_OPENROUTER_MODEL)
        try:
            result = await client.chat(
                [{"role": "user", "content": "hi"}],
                options=LLMCallOptions(
                    response_format=LlmResponseFormat.JSON_SCHEMA,
                    response_schema=SKILL_EXTRACTION_SCHEMA,
                ),
            )
        finally:
            del EXACT_RESPONSE_FORMAT_OVERRIDES[STRUCTURED_OPENROUTER_OVERRIDE_KEY]
            await client.close()
        assert result.structured_content is None
        assert result.text == raw_json

    @pytest.mark.asyncio
    async def test_options_tools_wins_over_tools_kwarg(self):
        """Verify options.tools wins over the tools keyword argument."""
        from backend.llm.call_options import LLMCallOptions

        received: dict = {}

        async def handler(request):
            body = json.loads(request.content)
            received["tools"] = body.get("tools")
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(
                    b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
                    b"data: [DONE]\n\n"
                ),
            )

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        try:
            kwarg_tool = {"type": "function", "function": {"name": "via_kwarg"}}
            option_tool = {"type": "function", "function": {"name": "via_option"}}
            await client.chat(
                [{"role": "user", "content": "hi"}],
                tools=[kwarg_tool],
                options=LLMCallOptions(tools=[option_tool]),
            )
        finally:
            await client.close()
        assert received["tools"] == [option_tool]


# --- Empty-response retry behaviour ---


class TestOpenRouterClientEmptyRetry:
    """Verify one-retry-on-empty + warning fallback for OpenRouter."""

    @pytest.mark.asyncio
    async def test_empty_first_response_triggers_retry_that_succeeds(self):
        """Verify an empty first structured response retries and returns the retry."""
        call_count = 0

        async def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                content = ""
            else:
                content = json.dumps(
                    {
                        "answer": "Recovered",
                        "sql": None,
                        "suggestions": [],
                    }
                )
            return httpx.Response(
                200,
                json={
                    "model": STRUCTURED_OPENROUTER_MODEL,
                    "choices": [{"message": {"role": "assistant", "content": content}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                },
            )

        EXACT_RESPONSE_FORMAT_OVERRIDES[STRUCTURED_OPENROUTER_OVERRIDE_KEY] = (
            LlmResponseFormat.JSON_SCHEMA
        )
        transport = httpx.MockTransport(handler)
        client = make_client(transport, model=STRUCTURED_OPENROUTER_MODEL)
        try:
            result = await client.chat([{"role": "user", "content": "Hi"}])
        finally:
            del EXACT_RESPONSE_FORMAT_OVERRIDES[STRUCTURED_OPENROUTER_OVERRIDE_KEY]
            await client.close()
        assert call_count == 2
        assert result.text == "Recovered"

    @pytest.mark.asyncio
    async def test_double_empty_response_surfaces_warning(self):
        """Verify a second empty structured response yields the warning."""
        call_count = 0

        async def handler(request):
            nonlocal call_count
            call_count += 1
            return httpx.Response(
                200,
                json={
                    "model": STRUCTURED_OPENROUTER_MODEL,
                    "choices": [{"message": {"role": "assistant", "content": ""}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 0},
                },
            )

        EXACT_RESPONSE_FORMAT_OVERRIDES[STRUCTURED_OPENROUTER_OVERRIDE_KEY] = (
            LlmResponseFormat.JSON_SCHEMA
        )
        transport = httpx.MockTransport(handler)
        client = make_client(transport, model=STRUCTURED_OPENROUTER_MODEL)
        try:
            result = await client.chat([{"role": "user", "content": "Hi"}])
        finally:
            del EXACT_RESPONSE_FORMAT_OVERRIDES[STRUCTURED_OPENROUTER_OVERRIDE_KEY]
            await client.close()
        assert call_count == 2
        from backend.llm.openrouter.client import EMPTY_LLM_RESPONSE_WARNING

        assert result.text == EMPTY_LLM_RESPONSE_WARNING
        assert result.structured_content is not None
        assert result.structured_content.answer == EMPTY_LLM_RESPONSE_WARNING
        assert result.tool_calls == []

    @pytest.mark.asyncio
    async def test_empty_streaming_response_triggers_retry(self):
        """Verify empty streamed text also retries once before warning."""
        call_count = 0

        async def handler(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                body = "data: [DONE]\n\n"
            else:
                body = (
                    'data: {"choices":[{"delta":{"content":"ok after retry"}}]}\n\n'
                    "data: [DONE]\n\n"
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=body.encode(),
            )

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        try:
            result = await client.chat([{"role": "user", "content": "Hi"}])
        finally:
            await client.close()
        assert call_count == 2
        assert result.text == "ok after retry"

    @pytest.mark.asyncio
    async def test_tool_call_response_does_not_trigger_retry(self):
        """Verify tool_calls with empty text skip retry (tool deferral)."""
        call_count = 0

        async def handler(request):
            nonlocal call_count
            call_count += 1
            body = json.loads(request.content)
            assert body["stream"] is True
            assert "response_format" not in body
            return httpx.Response(
                200,
                text=(
                    'data: {"model":"structured/test","choices":[{"delta":'
                    '{"tool_calls":[{"index":0,"id":"call_1","type":'
                    '"function","function":{"name":"web_search",'
                    '"arguments":"{\\"query\\":\\"hi\\"}"}}]}}]}\n\n'
                    "data: [DONE]\n\n"
                ),
            )

        EXACT_RESPONSE_FORMAT_OVERRIDES[STRUCTURED_OPENROUTER_OVERRIDE_KEY] = (
            LlmResponseFormat.JSON_SCHEMA
        )
        transport = httpx.MockTransport(handler)
        client = make_client(transport, model=STRUCTURED_OPENROUTER_MODEL)
        try:
            result = await client.chat(
                [{"role": "user", "content": "Hi"}],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "web_search",
                            "description": "Search",
                            "parameters": {"type": "object"},
                        },
                    }
                ],
            )
        finally:
            del EXACT_RESPONSE_FORMAT_OVERRIDES[STRUCTURED_OPENROUTER_OVERRIDE_KEY]
            await client.close()
        assert call_count == 1
        assert result.tool_calls[0].name == "web_search"

    @pytest.mark.asyncio
    async def test_malformed_streamed_tool_arguments_fail_closed(self):
        """Malformed native arguments cannot become an empty tool invocation."""

        async def handler(_request):
            return httpx.Response(
                200,
                text=(
                    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
                    '"id":"call_1","type":"function","function":{'
                    '"name":"web_search","arguments":"{bad"}}]}}]}\n\n'
                    "data: [DONE]\n\n"
                ),
            )

        client = make_client(httpx.MockTransport(handler))
        try:
            with pytest.raises(OpenRouterClientError, match="malformed tool call"):
                await client.chat(
                    [{"role": "user", "content": "search"}],
                    tools=[
                        {
                            "type": "function",
                            "function": {
                                "name": "web_search",
                                "description": "Search",
                                "parameters": {"type": "object"},
                            },
                        }
                    ],
                )
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_empty_streamed_tool_arguments_become_empty_object(self):
        """A zero-parameter tool call streams empty arguments and must run."""

        async def handler(_request):
            return httpx.Response(
                200,
                text=(
                    'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
                    '"id":"call_1","type":"function","function":{'
                    '"name":"system_status","arguments":""}}]}}]}\n\n'
                    "data: [DONE]\n\n"
                ),
            )

        client = make_client(httpx.MockTransport(handler))
        try:
            result = await client.chat(
                [{"role": "user", "content": "status"}],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "system_status",
                            "description": "Status",
                            "parameters": {"type": "object"},
                        },
                    }
                ],
            )
        finally:
            await client.close()
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].name == "system_status"
        assert result.tool_calls[0].arguments == {}


class TestOpenRouterStructuredStreaming:
    """Tests for OpenRouter JSON_SCHEMA + streaming path."""

    @pytest.mark.asyncio
    async def test_streaming_json_schema_emits_answer_deltas(self, monkeypatch):
        """Feed partial JSON SSE chunks; verify decoded answer reaches queue."""
        from backend.config import reset_settings

        monkeypatch.setenv("BIMEMO_LLM_RESPONSE_STREAM", "true")
        reset_settings()

        sse_chunks = [
            '{"choices":[{"delta":{"content":"{\\"answer\\":\\"hel"}}]}',
            '{"choices":[{"delta":{"content":"lo\\"}"}}]}',
            '{"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":4}}',
        ]
        sse_body = (
            "\n".join(f"data: {chunk}" for chunk in sse_chunks) + "\n\ndata: [DONE]\n"
        )

        async def handler(request):
            body = json.loads(request.content)
            assert body["stream"] is True
            assert body["response_format"]["type"] == "json_schema"
            return httpx.Response(
                200,
                text=sse_body,
                headers={"content-type": "text/event-stream"},
            )

        EXACT_RESPONSE_FORMAT_OVERRIDES[STRUCTURED_OPENROUTER_OVERRIDE_KEY] = (
            LlmResponseFormat.JSON_SCHEMA
        )
        transport = httpx.MockTransport(handler)
        client = make_client(transport, model=STRUCTURED_OPENROUTER_MODEL)
        stream_queue: asyncio.Queue = asyncio.Queue()
        try:
            result = await client.chat(
                [{"role": "user", "content": "Hi"}],
                stream_queue=stream_queue,
            )
        finally:
            del EXACT_RESPONSE_FORMAT_OVERRIDES[STRUCTURED_OPENROUTER_OVERRIDE_KEY]
            await client.close()
            reset_settings()

        token_events: list[str] = []
        while not stream_queue.empty():
            event = stream_queue.get_nowait()
            if event["type"] == "token":
                token_events.append(event["content"])
        assert "".join(token_events) == "hello"
        assert result.structured_content is not None
        assert result.structured_content.answer == "hello"
        assert result.text == "hello"

    @pytest.mark.asyncio
    async def test_non_streaming_json_schema_preserves_blocking_path(
        self,
        monkeypatch,
    ):
        """Confirm stream=false still calls the single-POST structured path."""
        from backend.config import reset_settings

        monkeypatch.setenv("BIMEMO_LLM_RESPONSE_STREAM", "false")
        reset_settings()

        async def handler(request):
            body = json.loads(request.content)
            assert body["stream"] is False
            return httpx.Response(
                200,
                json={
                    "model": STRUCTURED_OPENROUTER_MODEL,
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(
                                    {
                                        "answer": "blocking answer",
                                        "sql": None,
                                        "suggestions": [],
                                    }
                                ),
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2},
                },
            )

        EXACT_RESPONSE_FORMAT_OVERRIDES[STRUCTURED_OPENROUTER_OVERRIDE_KEY] = (
            LlmResponseFormat.JSON_SCHEMA
        )
        transport = httpx.MockTransport(handler)
        client = make_client(transport, model=STRUCTURED_OPENROUTER_MODEL)
        stream_queue: asyncio.Queue = asyncio.Queue()
        try:
            result = await client.chat(
                [{"role": "user", "content": "Hi"}],
                stream_queue=stream_queue,
            )
        finally:
            del EXACT_RESPONSE_FORMAT_OVERRIDES[STRUCTURED_OPENROUTER_OVERRIDE_KEY]
            await client.close()
            reset_settings()

        assert stream_queue.empty()
        assert result.structured_content is not None
        assert result.structured_content.answer == "blocking answer"
        assert result.text == "blocking answer"
