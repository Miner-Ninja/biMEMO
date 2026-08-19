"""Tests for OpenRouterProvider chat, embed, and streaming.

Covers health check, model verification (no-op), chat completion,
embed (empty), and streaming. Uses httpx MockTransport.
"""

import json

import httpx
import pytest

from backend.llm.ollama.models import ChatMessage, ChatResponse
from backend.llm.openrouter.provider import (
    OpenRouterError,
    OpenRouterProvider,
)


def make_provider(transport):
    """Create a provider with mock transport."""
    provider = OpenRouterProvider(
        api_key="test-api-key",
        primary_model="qwen/qwen3-14b",
        dialog_recorder_model="qwen/qwen3-4b",
    )
    provider._client = httpx.AsyncClient(
        transport=transport,
        base_url="https://openrouter.ai",
        headers={"Authorization": "Bearer test-api-key"},
    )
    return provider


def make_split_provider(primary_transport, dialog_recorder_transport):
    """Create a provider with separate primary and dialog recorder endpoints."""
    provider = OpenRouterProvider(
        api_key="",
        primary_model="Qwen/Qwen3.5-9B",
        dialog_recorder_model="Qwen/Qwen3.5-2B",
        base_url="http://vllm-primary:8000",
        dialog_recorder_base_url="http://vllm-dialog-recorder:8000",
        api_prefix="/v1",
    )
    provider._client = httpx.AsyncClient(
        transport=primary_transport,
        base_url="http://vllm-primary:8000",
        headers={},
    )
    provider._dialog_recorder_client = httpx.AsyncClient(
        transport=dialog_recorder_transport,
        base_url="http://vllm-dialog-recorder:8000",
        headers={},
    )
    return provider


class TestHealthCheck:
    """Tests for OpenRouter health checking."""

    async def test_healthy(self):
        """Verify health check returns True when OK."""

        async def handler(request):
            return httpx.Response(200, json={"data": []})

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)
        assert await provider.check_health()
        await provider.close()

    async def test_unhealthy(self):
        """Verify health check returns False on error."""

        async def handler(request):
            raise httpx.ConnectError("refused")

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)
        assert not await provider.check_health()
        await provider.close()

    async def test_unhealthy_on_500(self):
        """Verify health check returns False on server error."""

        async def handler(request):
            return httpx.Response(500, text="error")

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)
        assert not await provider.check_health()
        await provider.close()

    async def test_custom_provider_omits_auth_header_when_api_key_is_empty(self):
        """Verify custom/vLLM requests do not send an empty Bearer header."""

        async def handler(request):
            assert "authorization" not in request.headers
            return httpx.Response(200, json={"data": []})

        provider = make_split_provider(
            httpx.MockTransport(handler),
            httpx.MockTransport(handler),
        )
        assert await provider.check_health()
        await provider.close()


class TestVerifyModels:
    """Tests for model verification (no-op)."""

    async def test_verify_succeeds(self):
        """Verify verify_models is a no-op that succeeds."""
        provider = OpenRouterProvider(
            api_key="test-key",
            primary_model="qwen/qwen3-14b",
            dialog_recorder_model="qwen/qwen3-4b",
        )
        await provider.verify_models()
        await provider.close()


class TestChat:
    """Tests for non-streaming chat completion."""

    async def test_returns_chat_response(self):
        """Verify chat returns ChatResponse with correct fields."""

        async def handler(request):
            body = json.loads(request.content)
            assert body["model"] == "qwen/qwen3-14b"
            assert body["stream"] is False
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "Hello!"}}],
                    "model": "qwen/qwen3-14b",
                },
            )

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)
        messages = [ChatMessage(role="user", content="Hi")]
        response = await provider.chat(messages)
        assert isinstance(response, ChatResponse)
        assert response.content == "Hello!"
        assert response.model == "qwen/qwen3-14b"
        assert response.done is True
        await provider.close()

    async def test_chat_with_custom_model(self):
        """Verify chat uses specified model."""

        async def handler(request):
            body = json.loads(request.content)
            assert body["model"] == "qwen/qwen3-4b"
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "Observed"}}],
                    "model": "qwen/qwen3-4b",
                },
            )

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)
        messages = [ChatMessage(role="user", content="Hi")]
        response = await provider.chat(messages, model="qwen/qwen3-4b")
        assert response.content == "Observed"
        await provider.close()

    async def test_chat_forwards_generation_limits(self):
        """Verify chat forwards max_tokens and temperature overrides."""

        async def handler(request):
            body = json.loads(request.content)
            assert body["max_tokens"] == 160
            assert body["temperature"] == 0.0
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "Short"}}],
                    "model": "qwen/qwen3-14b",
                },
            )

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)
        messages = [ChatMessage(role="user", content="Hi")]
        response = await provider.chat(
            messages,
            max_tokens=160,
            temperature=0.0,
        )
        assert response.content == "Short"
        await provider.close()

    async def test_custom_provider_disables_thinking_when_requested(self):
        """Verify custom/vLLM chat forwards the request-level thinking flag."""

        async def primary_handler(request):
            body = json.loads(request.content)
            assert body["chat_template_kwargs"] == {
                "enable_thinking": False,
            }
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "Observed"}}],
                    "model": "Qwen/Qwen3.5-9B",
                },
            )

        provider = make_split_provider(
            httpx.MockTransport(primary_handler),
            httpx.MockTransport(primary_handler),
        )
        response = await provider.chat(
            [ChatMessage(role="user", content="Hi")],
            enable_thinking=False,
        )
        assert response.content == "Observed"
        await provider.close()

    async def test_chat_empty_choices(self):
        """Verify chat handles empty choices gracefully."""

        async def handler(request):
            return httpx.Response(
                200,
                json={
                    "choices": [],
                    "model": "qwen/qwen3-14b",
                },
            )

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)
        messages = [ChatMessage(role="user", content="Hi")]
        response = await provider.chat(messages)
        assert response.content == ""
        await provider.close()

    async def test_chat_error(self):
        """Verify OpenRouterError on API failure."""

        async def handler(request):
            return httpx.Response(500, text="error")

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)
        with pytest.raises(OpenRouterError, match="Chat request"):
            await provider.chat([ChatMessage(role="user", content="Hi")])
        await provider.close()

    async def test_connection_error(self):
        """Verify OpenRouterError on connection failure."""

        async def handler(request):
            raise httpx.ConnectError("refused")

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)
        with pytest.raises(OpenRouterError, match="Chat request"):
            await provider.chat([ChatMessage(role="user", content="Hi")])
        await provider.close()

    async def test_dialog_recorder_model_uses_dialog_recorder_endpoint(self):
        """Verify dialog recorder model requests use the dialog recorder base URL."""

        async def primary_handler(request):
            raise AssertionError("primary must not handle recorder calls")

        async def dialog_recorder_handler(request):
            assert str(request.url).endswith("/v1/chat/completions")
            body = json.loads(request.content)
            assert body["model"] == "Qwen/Qwen3.5-2B"
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "Observed"}}],
                    "model": "Qwen/Qwen3.5-2B",
                },
            )

        provider = make_split_provider(
            httpx.MockTransport(primary_handler),
            httpx.MockTransport(dialog_recorder_handler),
        )
        response = await provider.chat(
            [ChatMessage(role="user", content="Hi")],
            model="Qwen/Qwen3.5-2B",
        )
        assert response.content == "Observed"
        await provider.close()

    async def test_custom_provider_merges_system_messages(self):
        """Verify custom/vLLM chat collapses system messages for strict APIs."""

        async def primary_handler(request):
            body = json.loads(request.content)
            assert body["messages"] == [
                {
                    "role": "system",
                    "content": "System A\n\nSystem B",
                },
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello"},
            ]
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "Observed"}}],
                    "model": "Qwen/Qwen3.5-9B",
                },
            )

        provider = make_split_provider(
            httpx.MockTransport(primary_handler),
            httpx.MockTransport(primary_handler),
        )
        response = await provider.chat(
            [
                ChatMessage(role="system", content="System A"),
                ChatMessage(role="user", content="Hi"),
                ChatMessage(role="system", content="System B"),
                ChatMessage(role="assistant", content="Hello"),
            ]
        )
        assert response.content == "Observed"
        await provider.close()

    async def test_custom_provider_strips_reasoning_from_chat_output(self):
        """Verify custom/vLLM provider removes leaked reasoning from chat output."""

        async def primary_handler(request):
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": "Reasoning\n</think>\n\nObserved",
                            }
                        }
                    ],
                    "model": "Qwen/Qwen3.5-9B",
                },
            )

        provider = make_split_provider(
            httpx.MockTransport(primary_handler),
            httpx.MockTransport(primary_handler),
        )
        response = await provider.chat([ChatMessage(role="user", content="Hi")])
        assert response.content == "Observed"
        await provider.close()


class TestEmbed:
    """Tests for embedding via OpenAI-compatible /v1/embeddings."""

    async def test_returns_empty_list_when_no_model_configured(self):
        """Verify embed returns empty list when embedding_model is unset."""
        provider = OpenRouterProvider(
            api_key="test-key",
            primary_model="qwen/qwen3-14b",
            dialog_recorder_model="qwen/qwen3-4b",
        )
        result = await provider.embed("test text")
        assert result == []
        await provider.close()

    async def test_embed_returns_empty_when_model_arg_empty(self):
        """Verify embed returns empty when neither field nor arg has a model."""
        provider = OpenRouterProvider(
            api_key="test-key",
            primary_model="qwen/qwen3-14b",
            dialog_recorder_model="qwen/qwen3-4b",
        )
        result = await provider.embed("test", model="")
        assert result == []
        await provider.close()

    async def test_embed_targets_dedicated_embedding_base_url(self):
        """Verify embed posts to embedding_base_url when configured."""
        captured: dict[str, object] = {}

        async def embed_handler(request):
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={"data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]},
            )

        async def chat_handler(request):
            captured["chat_called"] = True
            return httpx.Response(500, text="chat must not be called")

        provider = OpenRouterProvider(
            api_key="",
            primary_model="Qwen/Qwen3.5-9B",
            dialog_recorder_model="Qwen/Qwen3.5-2B",
            base_url="http://vllm-primary:8000",
            embedding_base_url="http://tei:80",
            api_prefix="/v1",
            embedding_model="BAAI/bge-m3",
        )
        provider._client = httpx.AsyncClient(
            transport=httpx.MockTransport(chat_handler),
            base_url="http://vllm-primary:8000",
            headers={},
        )
        provider._embedding_client = httpx.AsyncClient(
            transport=httpx.MockTransport(embed_handler),
            base_url="http://tei:80",
            headers={},
        )

        result = await provider.embed("dimension probe")

        assert result == [0.1, 0.2, 0.3, 0.4]
        assert "chat_called" not in captured
        assert captured["url"].endswith("/v1/embeddings")
        assert captured["body"] == {
            "model": "BAAI/bge-m3",
            "input": "dimension probe",
        }
        await provider.close()

    async def test_embed_falls_back_to_base_url_when_no_dedicated_url(self):
        """Verify embed targets base_url when no embedding_base_url is set."""
        captured: dict[str, object] = {}

        async def handler(request):
            captured["url"] = str(request.url)
            return httpx.Response(
                200,
                json={"data": [{"embedding": [0.5]}]},
            )

        provider = OpenRouterProvider(
            api_key="",
            primary_model="Qwen/Qwen3.5-9B",
            dialog_recorder_model="Qwen/Qwen3.5-2B",
            base_url="http://vllm-primary:8000",
            api_prefix="/v1",
            embedding_model="BAAI/bge-m3",
        )
        provider._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://vllm-primary:8000",
            headers={},
        )

        result = await provider.embed("hello")

        assert result == [0.5]
        assert "vllm-primary" in captured["url"]
        await provider.close()

    async def test_embed_returns_empty_on_http_error(self):
        """Verify embed gracefully degrades when the upstream returns an error."""

        async def handler(request):
            raise httpx.ConnectError("refused")

        provider = OpenRouterProvider(
            api_key="",
            primary_model="Qwen/Qwen3.5-9B",
            dialog_recorder_model="Qwen/Qwen3.5-2B",
            base_url="http://vllm-primary:8000",
            api_prefix="/v1",
            embedding_model="BAAI/bge-m3",
        )
        provider._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://vllm-primary:8000",
            headers={},
        )

        result = await provider.embed("hello")

        assert result == []
        await provider.close()


class TestChatStream:
    """Tests for streaming chat completion."""

    async def test_yields_chunks(self):
        """Verify streaming yields content chunks."""
        lines = [
            'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            'data: {"choices":[{"delta":{"content":" world"}}]}',
            "data: [DONE]",
        ]

        async def handler(request):
            body = json.loads(request.content)
            assert body["stream"] is True
            return httpx.Response(200, text="\n".join(lines))

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)
        messages = [ChatMessage(role="user", content="Hi")]
        chunks = []
        async for chunk in provider.chat_stream(messages):
            chunks.append(chunk)
        assert chunks == ["Hello", " world"]
        await provider.close()

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
        provider = make_provider(transport)
        chunks = []
        async for chunk in provider.chat_stream(
            [ChatMessage(role="user", content="Hi")]
        ):
            chunks.append(chunk)
        assert chunks == ["Hello"]
        await provider.close()

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
        provider = make_provider(transport)
        chunks = []
        async for chunk in provider.chat_stream(
            [ChatMessage(role="user", content="Hi")]
        ):
            chunks.append(chunk)
        assert chunks == ["ok"]
        await provider.close()

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
        provider = make_provider(transport)
        chunks = []
        async for chunk in provider.chat_stream(
            [ChatMessage(role="user", content="test")]
        ):
            chunks.append(chunk)
        assert chunks == ["Hi"]
        await provider.close()

    async def test_stream_error(self):
        """Verify OpenRouterError on stream failure."""

        async def handler(request):
            raise httpx.ConnectError("refused")

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)
        with pytest.raises(OpenRouterError, match="Stream request"):
            async for _ in provider.chat_stream(
                [ChatMessage(role="user", content="Hi")]
            ):
                pass
        await provider.close()


class TestClose:
    """Tests for close method."""

    async def test_close_works(self):
        """Verify close does not raise."""
        provider = OpenRouterProvider(
            api_key="test-key",
            primary_model="qwen/qwen3-14b",
            dialog_recorder_model="qwen/qwen3-4b",
        )
        await provider.close()
