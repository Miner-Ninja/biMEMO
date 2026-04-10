"""Tests for OpenRouterClient SSE streaming and LLM call factory.

Covers SSE parsing, token forwarding, close, and make_llm_call.
Uses httpx MockTransport to avoid real network calls.
"""

import asyncio
import json

import httpx
import pytest
from backend.llm.call_factory import LLM_CALL_TIMEOUT, LLMResponse, make_llm_call
from backend.llm.openrouter.client import (
    OpenRouterClient,
    OpenRouterClientError,
)
from backend.llm.reasoning import strip_reasoning_output


def make_client(transport, model="qwen/qwen3-14b"):
    """Create a client with mock transport."""
    client = OpenRouterClient(
        api_key="test-api-key",
        model=model,
    )
    client._client = httpx.AsyncClient(
        transport=transport,
        base_url="https://openrouter.ai",
        headers={"Authorization": "Bearer test-api-key"},
    )
    return client


def make_custom_client(transport, model="Qwen/Qwen3.5-9B", num_ctx=None):
    """Create a custom-provider client with OpenAI-style /v1 paths."""
    client = OpenRouterClient(
        api_key="",
        model=model,
        base_url="http://vllm-primary:8000",
        api_prefix="/v1",
        num_ctx=num_ctx,
    )
    client._client = httpx.AsyncClient(
        transport=transport,
        base_url="http://vllm-primary:8000",
        headers={},
    )
    return client


class TestOpenRouterClientInit:
    """Tests for OpenRouterClient initialization."""

    def test_sets_bearer_header(self):
        """Verify Authorization header is set."""
        client = OpenRouterClient(
            api_key="my-key",
            model="qwen/qwen3-14b",
        )
        auth = client._client.headers["authorization"]
        assert auth == "Bearer my-key"

    def test_sets_base_url(self):
        """Verify base URL points to OpenRouter."""
        client = OpenRouterClient(
            api_key="my-key",
            model="qwen/qwen3-14b",
        )
        assert str(client._client.base_url) == "https://openrouter.ai"

    def test_omits_auth_header_when_api_key_is_empty(self):
        """Verify empty API keys do not create an invalid Bearer header."""
        client = OpenRouterClient(
            api_key="",
            model="Qwen/Qwen3.5-9B",
            base_url="http://vllm-primary:8000",
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
            assert body["model"] == "qwen/qwen3-14b"
            assert body["stream"] is True
            return httpx.Response(200, text="\n".join(lines))

        transport = httpx.MockTransport(handler)
        client = make_client(transport)
        result = await client.chat([{"role": "user", "content": "Hi"}])
        assert isinstance(result, LLMResponse)
        assert result.text == "Hello world"
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
            (
                'data: {"choices":[{"delta":'
                '{"reasoning_content":"internal plan"}}]}'
            ),
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
            sse_queue=queue,
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
    async def test_forwards_to_sse_queue(self):
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
            sse_queue=queue,
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
        llm_call = make_llm_call(client, sse_queue=queue)
        await llm_call([{"role": "user", "content": "Hi"}])
        assert queue.qsize() == 1
        await client.close()

    def test_timeout_constant(self):
        """Verify LLM call timeout is 5 minutes."""
        assert LLM_CALL_TIMEOUT == 300.0
