"""Tests for OllamaProvider chat completion and model management.

Covers health checks, model listing, model verification,
chat completion, streaming, compute options, and error handling.
Uses httpx MockTransport to avoid real network calls.
"""

import json

import httpx
import pytest

from backend.llm.call_factory import LLM_CALL_TIMEOUT
from backend.llm.capabilities import ModelCapabilities
from backend.llm.capabilities_registry import CapabilitiesRegistry
from backend.llm.compute_config import (
    ComputeConfig,
    ComputeMode,
)
from backend.llm.ollama.models import (
    ChatMessage,
    ModelNotAvailableError,
    OllamaError,
)
from backend.llm.ollama.provider import (
    OllamaProvider,
)


# --- Test fixtures ---
TEST_PRIMARY_MODEL = "qwen3:8b"
TEST_RECORDER_MODEL = "qwen3:1.7b"
TEST_NUM_CTX = 32768


def _prebuilt_registry(models: tuple[str, ...]) -> CapabilitiesRegistry:
    """Build a registry pre-populated with canned capabilities for test models."""
    registry = CapabilitiesRegistry()
    for model in models:
        registry._cache[("ollama", model)] = ModelCapabilities(
            provider="ollama",
            model=model,
            max_context_tokens=TEST_NUM_CTX,
            source="test:prebuilt",
        )
    return registry


def make_provider(
    transport,
    compute_mode=ComputeMode.GPU_ONLY,
    gpu_layers=0,
    dialog_recorder_transport=None,
):
    """Create a provider with a mock transport and pre-populated registry."""
    config = ComputeConfig(mode=compute_mode, gpu_layers=gpu_layers)
    provider = OllamaProvider(
        base_url="http://localhost:11434",
        dialog_recorder_base_url=(
            "http://localhost:11435" if dialog_recorder_transport else None
        ),
        primary_model=TEST_PRIMARY_MODEL,
        dialog_recorder_model=TEST_RECORDER_MODEL,
        compute_config=config,
        capabilities_registry=_prebuilt_registry(
            (TEST_PRIMARY_MODEL, TEST_RECORDER_MODEL),
        ),
    )
    provider._client = httpx.AsyncClient(
        transport=transport,
        base_url="http://localhost:11434",
    )
    if dialog_recorder_transport is not None:
        provider._dialog_recorder_client = httpx.AsyncClient(
            transport=dialog_recorder_transport,
            base_url="http://localhost:11435",
        )
    return provider


class TestProviderInitialization:
    """Tests for OllamaProvider initialization."""

    def test_default_timeout_matches_llm_call_timeout(self):
        """Verify background Ollama work uses the shared LLM timeout."""
        provider = make_provider(
            httpx.MockTransport(lambda request: httpx.Response(200))
        )
        assert provider.timeout == LLM_CALL_TIMEOUT

    def test_configured_num_ctx_is_added_to_options(self):
        """Verify capabilities num_ctx is always forwarded into options."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            primary_model="qwen3:8b",
            dialog_recorder_model="qwen3:1.7b",
            compute_config=ComputeConfig(mode=ComputeMode.GPU_ONLY, gpu_layers=0),
            capabilities_registry=CapabilitiesRegistry(),
        )
        caps = ModelCapabilities(
            provider="ollama",
            model="qwen3:8b",
            max_context_tokens=16384,
            source="test",
        )
        assert provider._build_options(caps) == {"num_ctx": 16384}

    def test_chat_options_include_generation_limits(self):
        """Verify chat-specific options include output limits."""
        provider = OllamaProvider(
            base_url="http://localhost:11434",
            primary_model="qwen3:8b",
            dialog_recorder_model="qwen3:1.7b",
            compute_config=ComputeConfig(mode=ComputeMode.GPU_ONLY, gpu_layers=0),
            capabilities_registry=CapabilitiesRegistry(),
        )
        caps = ModelCapabilities(
            provider="ollama",
            model="qwen3:8b",
            max_context_tokens=16384,
            source="test",
        )
        assert provider._build_chat_options(
            caps,
            max_tokens=160,
            temperature=0.0,
        ) == {
            "num_ctx": 16384,
            "num_predict": 160,
            "temperature": 0.0,
        }

    async def test_chat_ignores_enable_thinking_flag(self):
        """Verify provider parity flag does not break Ollama chat requests."""

        async def handler(request):
            body = json.loads(request.content.decode())
            assert "messages" in body
            return httpx.Response(
                200,
                json={
                    "message": {"content": "Short"},
                    "done": True,
                    "model": "qwen3:8b",
                },
            )

        provider = make_provider(httpx.MockTransport(handler))
        response = await provider.chat(
            [ChatMessage(role="user", content="Hi")],
            enable_thinking=False,
        )
        assert response.content == "Short"
        await provider.close()


class TestHealthCheck:
    """Tests for Ollama health checking."""

    async def test_healthy(self):
        """Verify health check returns True when OK."""

        async def handler(request):
            return httpx.Response(200, text="Ollama is running")

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


class TestListModels:
    """Tests for model listing."""

    async def test_lists_models(self):
        """Verify models are listed from API response."""

        async def handler(request):
            if request.url.path == "/api/tags":
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            {"name": "qwen3:8b"},
                            {"name": "qwen3:1.7b"},
                        ]
                    },
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)

        models = await provider.list_models()
        assert models == ["qwen3:8b", "qwen3:1.7b"]
        await provider.close()

    async def test_list_models_error(self):
        """Verify OllamaError on API failure."""

        async def handler(request):
            return httpx.Response(500, text="error")

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)

        with pytest.raises(OllamaError, match="Failed to list"):
            await provider.list_models()
        await provider.close()


class TestVerifyModels:
    """Tests for model availability verification."""

    async def test_verify_available(self):
        """Verify passes when all models are available."""

        async def handler(request):
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "qwen3:8b"},
                        {"name": "qwen3:1.7b"},
                    ]
                },
            )

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)

        await provider.verify_models()
        await provider.close()

    async def test_verify_missing_primary(self):
        """Verify error when primary model is missing."""

        async def handler(request):
            return httpx.Response(
                200,
                json={"models": [{"name": "qwen3:1.7b"}]},
            )

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)

        with pytest.raises(ModelNotAvailableError, match="qwen3:8b"):
            await provider.verify_models()
        await provider.close()

    async def test_verify_missing_dialog_recorder(self):
        """Verify error when dialog recorder model is missing."""

        async def handler(request):
            return httpx.Response(
                200,
                json={"models": [{"name": "qwen3:8b"}]},
            )

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)

        with pytest.raises(ModelNotAvailableError, match=r"qwen3:1\.7b"):
            await provider.verify_models()
        await provider.close()

    async def test_verify_uses_separate_dialog_recorder_endpoint(self):
        """Verify recorder model lookup uses the recorder endpoint."""

        async def primary_handler(request):
            assert str(request.url) == "http://localhost:11434/api/tags"
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})

        async def dialog_recorder_handler(request):
            assert str(request.url) == "http://localhost:11435/api/tags"
            return httpx.Response(200, json={"models": [{"name": "qwen3:1.7b"}]})

        provider = make_provider(
            httpx.MockTransport(primary_handler),
            dialog_recorder_transport=httpx.MockTransport(dialog_recorder_handler),
        )

        await provider.verify_models()
        await provider.close()

    async def test_verify_with_latest_suffix(self):
        """Verify matches models with :latest suffix."""

        async def handler(request):
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "qwen3:8b:latest"},
                        {"name": "qwen3:1.7b:latest"},
                    ]
                },
            )

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)

        await provider.verify_models()
        await provider.close()


class TestChat:
    """Tests for chat completion."""

    async def test_chat_response(self):
        """Verify chat returns parsed response."""

        async def handler(request):
            body = json.loads(request.content)
            assert body["model"] == "qwen3:8b"
            assert body["stream"] is False
            assert len(body["messages"]) == 1
            return httpx.Response(
                200,
                json={
                    "message": {"content": "Hello!"},
                    "model": "qwen3:8b",
                    "done": True,
                },
            )

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)

        messages = [ChatMessage(role="user", content="Hi")]
        response = await provider.chat(messages)
        assert response.content == "Hello!"
        assert response.model == "qwen3:8b"
        assert response.done
        await provider.close()

    async def test_chat_with_dialog_recorder_model(self):
        """Verify chat uses dialog recorder model."""

        async def handler(request):
            body = json.loads(request.content)
            assert body["model"] == "qwen3:1.7b"
            return httpx.Response(
                200,
                json={
                    "message": {"content": "Observed"},
                    "model": "qwen3:1.7b",
                    "done": True,
                },
            )

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)

        messages = [ChatMessage(role="user", content="Hi")]
        response = await provider.chat(messages, model="qwen3:1.7b")
        assert response.content == "Observed"
        await provider.close()

    async def test_chat_forwards_generation_limits(self):
        """Verify chat sends max token and temperature overrides."""

        async def handler(request):
            body = json.loads(request.content)
            assert body["options"]["num_predict"] == 160
            assert body["options"]["temperature"] == 0.0
            return httpx.Response(
                200,
                json={
                    "message": {"content": "Hello!"},
                    "model": "qwen3:8b",
                    "done": True,
                },
            )

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)

        response = await provider.chat(
            [ChatMessage(role="user", content="Hi")],
            max_tokens=160,
            temperature=0.0,
        )
        assert response.content == "Hello!"
        await provider.close()

    async def test_chat_with_dialog_recorder_model_uses_dialog_recorder_endpoint(self):
        """Verify recorder chat uses the recorder endpoint."""

        async def primary_handler(request):
            raise AssertionError("Primary must not receive recorder chat")

        async def dialog_recorder_handler(request):
            body = json.loads(request.content)
            assert str(request.url) == "http://localhost:11435/api/chat"
            assert body["model"] == "qwen3:1.7b"
            return httpx.Response(
                200,
                json={
                    "message": {"content": "Observed"},
                    "model": "qwen3:1.7b",
                    "done": True,
                },
            )

        provider = make_provider(
            httpx.MockTransport(primary_handler),
            dialog_recorder_transport=httpx.MockTransport(dialog_recorder_handler),
        )

        messages = [ChatMessage(role="user", content="Hi")]
        response = await provider.chat(messages, model="qwen3:1.7b")
        assert response.content == "Observed"
        await provider.close()

    async def test_chat_converts_multimodal_messages_for_ollama(self):
        """Verify multimodal content is rewritten into Ollama's image format."""

        async def handler(request):
            body = json.loads(request.content)
            assert body["messages"][0]["content"] == "describe image"
            assert body["messages"][0]["images"] == ["ZmFrZS1pbWFnZQ=="]
            return httpx.Response(
                200,
                json={
                    "message": {"content": "image processed"},
                    "model": "qwen3:8b",
                    "done": True,
                },
            )

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)

        messages = [
            ChatMessage(
                role="user",
                content=[
                    {"type": "text", "text": "describe image"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,ZmFrZS1pbWFnZQ=="},
                    },
                ],
            )
        ]
        response = await provider.chat(messages)
        assert response.content == "image processed"
        await provider.close()

    async def test_chat_error(self):
        """Verify OllamaError on API failure."""

        async def handler(request):
            return httpx.Response(500, text="error")

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)

        with pytest.raises(OllamaError, match="Chat request"):
            await provider.chat([ChatMessage(role="user", content="Hi")])
        await provider.close()


class TestChatStream:
    """Tests for streaming chat completion."""

    async def test_stream_chunks(self):
        """Verify streaming yields content chunks."""
        lines = [
            json.dumps(
                {
                    "message": {"content": "Hello"},
                    "done": False,
                }
            ),
            json.dumps(
                {
                    "message": {"content": " world"},
                    "done": True,
                }
            ),
        ]

        async def handler(request):
            body = json.loads(request.content)
            assert body["stream"] is True
            content = "\n".join(lines)
            return httpx.Response(200, text=content)

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)

        messages = [ChatMessage(role="user", content="Hi")]
        chunks = []
        async for chunk in provider.chat_stream(messages):
            chunks.append(chunk)

        assert chunks == ["Hello", " world"]
        await provider.close()

    async def test_stream_empty_lines_skipped(self):
        """Verify empty lines in stream are skipped."""
        lines = [
            "",
            json.dumps(
                {
                    "message": {"content": "Hi"},
                    "done": True,
                }
            ),
        ]

        async def handler(request):
            content = "\n".join(lines)
            return httpx.Response(200, text=content)

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)

        chunks = []
        async for chunk in provider.chat_stream(
            [ChatMessage(role="user", content="Hi")]
        ):
            chunks.append(chunk)

        assert chunks == ["Hi"]
        await provider.close()

    async def test_stream_empty_content_skipped(self):
        """Verify chunks with empty content are skipped."""
        lines = [
            json.dumps({"message": {"content": ""}, "done": False}),
            json.dumps(
                {
                    "message": {"content": "Hello"},
                    "done": True,
                }
            ),
        ]

        async def handler(request):
            content = "\n".join(lines)
            return httpx.Response(200, text=content)

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)

        chunks = []
        async for chunk in provider.chat_stream(
            [ChatMessage(role="user", content="Hi")]
        ):
            chunks.append(chunk)

        assert chunks == ["Hello"]
        await provider.close()

    async def test_stream_ends_without_done(self):
        """Verify stream ends when no more lines."""
        lines = [
            json.dumps(
                {
                    "message": {"content": "Only"},
                    "done": False,
                }
            ),
        ]

        async def handler(request):
            content = "\n".join(lines)
            return httpx.Response(200, text=content)

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)

        chunks = []
        async for chunk in provider.chat_stream(
            [ChatMessage(role="user", content="Hi")]
        ):
            chunks.append(chunk)

        assert chunks == ["Only"]
        await provider.close()

    async def test_stream_error(self):
        """Verify OllamaError on stream failure."""

        async def handler(request):
            raise httpx.ConnectError("refused")

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)

        with pytest.raises(OllamaError, match="Stream request"):
            async for _ in provider.chat_stream(
                [ChatMessage(role="user", content="Hi")]
            ):
                pass
        await provider.close()


class TestEmbed:
    """Tests for embedding generation."""

    async def test_embed_returns_vector(self):
        """Verify embed returns float vector."""

        async def handler(request):
            body = json.loads(request.content)
            assert body["model"] == "qwen3:1.7b"
            assert body["input"] == "test text"
            return httpx.Response(
                200,
                json={
                    "embeddings": [[0.1, 0.2, 0.3]],
                },
            )

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)

        vec = await provider.embed("test text")
        assert vec == [0.1, 0.2, 0.3]
        await provider.close()

    async def test_embed_custom_model(self):
        """Verify embed uses specified model."""

        async def handler(request):
            body = json.loads(request.content)
            assert body["model"] == "custom-embed"
            return httpx.Response(
                200,
                json={"embeddings": [[1.0, 2.0]]},
            )

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)

        vec = await provider.embed("test", model="custom-embed")
        assert vec == [1.0, 2.0]
        await provider.close()

    async def test_embed_uses_configured_embedding_model(self):
        """hybrid-recall S1.1 — embed uses embedding_model, not the chat model."""

        async def handler(request):
            body = json.loads(request.content)
            assert body["model"] == "nomic-embed-text"
            return httpx.Response(200, json={"embeddings": [[0.5]]})

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)
        provider.embedding_model = "nomic-embed-text"

        vec = await provider.embed("test text")
        assert vec == [0.5]
        await provider.close()

    async def test_embed_error(self):
        """Verify OllamaError on embed failure."""

        async def handler(request):
            return httpx.Response(500, text="error")

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport)

        with pytest.raises(OllamaError, match="Embed request"):
            await provider.embed("test")
        await provider.close()


class TestComputeOptions:
    """Tests for compute mode options."""

    async def test_gpu_only_no_options(self):
        """Verify GPU-only mode sends no num_gpu option."""

        async def handler(request):
            body = json.loads(request.content)
            assert "num_gpu" not in body.get("options", {})
            return httpx.Response(
                200,
                json={
                    "message": {"content": "ok"},
                    "done": True,
                },
            )

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport, ComputeMode.GPU_ONLY)

        await provider.chat([ChatMessage(role="user", content="Hi")])
        await provider.close()

    async def test_mixed_mode_sends_num_gpu(self):
        """Verify mixed mode sends num_gpu option."""

        async def handler(request):
            body = json.loads(request.content)
            assert body["options"]["num_gpu"] == 20
            return httpx.Response(
                200,
                json={
                    "message": {"content": "ok"},
                    "done": True,
                },
            )

        transport = httpx.MockTransport(handler)
        provider = make_provider(transport, ComputeMode.MIXED, gpu_layers=20)

        await provider.chat([ChatMessage(role="user", content="Hi")])
        await provider.close()
