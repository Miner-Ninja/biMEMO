"""Tests for OllamaClient application-level Ollama access.

Covers model checking, model pulling, streaming chat,
SSE token forwarding, timeout handling, connection error
handling, and the lazily constructed provider property.
Uses httpx MockTransport.
"""

import asyncio
import json

import httpx
import pytest
from backend.llm.call_factory import LLM_CALL_TIMEOUT, make_llm_call
from backend.llm.capabilities import LlmResponseFormat
from backend.llm.capabilities_registry import CapabilitiesRegistry
from backend.llm.compute_config import (
    ComputeConfig,
    ComputeMode,
)
from backend.llm.hardware import (
    DialogRecorderMode,
    HardwareProfile,
    ProfileConfig,
)
from backend.llm.ollama.client import (
    OllamaClient,
    OllamaClientError,
)
from backend.llm.ollama.provider import OllamaProvider


# --- Test constants ---
DEFAULT_TEST_NUM_CTX = 32768
LARGE_TEST_NUM_CTX = 262144
OLLAMA_PROVIDER_NAME = "ollama"
PREBUILT_CAPABILITIES_SOURCE = "test:prebuilt"


def _prebuilt_registry(
    model: str,
    ctx: int = DEFAULT_TEST_NUM_CTX,
    response_format: LlmResponseFormat = LlmResponseFormat.TEXT,
):
    """Return a CapabilitiesRegistry pre-populated for the given test model."""
    from backend.llm.capabilities import ModelCapabilities
    from backend.llm.capabilities_registry import CapabilitiesRegistry

    registry = CapabilitiesRegistry()
    registry._cache[(OLLAMA_PROVIDER_NAME, model)] = ModelCapabilities(
        provider=OLLAMA_PROVIDER_NAME,
        model=model,
        max_context_tokens=ctx,
        source=PREBUILT_CAPABILITIES_SOURCE,
        llm_response_format=response_format,
    )
    return registry


def make_client(transport, tmp_path, model="qwen3:8b"):
    """Create a client with mock transport and token file."""
    token_file = tmp_path / "ollama-token"
    token_file.write_text("test-bearer-token\n")
    client = OllamaClient(
        base_url="http://caddy:8080",
        model=model,
        token_path=str(token_file),
        capabilities_registry=_prebuilt_registry(model),
    )
    client._client = httpx.AsyncClient(
        transport=transport,
        base_url="http://caddy:8080",
        headers={"Authorization": "Bearer test-bearer-token"},
    )
    return client


def make_runtime_ps_response(model: str = "qwen3:8b", context_length: int = 20480):
    """Build one ``/api/ps`` response for a loaded Ollama model."""
    return httpx.Response(
        200,
        json={
            "models": [
                {
                    "name": model,
                    "context_length": context_length,
                }
            ]
        },
    )


class TestOllamaClientInit:
    """Tests for OllamaClient initialization."""

    def test_default_timeout_matches_llm_call_timeout(self, tmp_path):
        """Verify the direct Ollama client waits as long as the orchestrator."""
        token_file = tmp_path / "token"
        token_file.write_text("my-secret-token\n")

        client = OllamaClient(
            base_url="http://caddy:8080",
            model="qwen3:8b",
            capabilities_registry=CapabilitiesRegistry(),
            token_path=str(token_file),
        )
        assert client.timeout == LLM_CALL_TIMEOUT

    def test_configured_num_ctx_is_exposed_in_usage(self, tmp_path):
        """Verify an explicit num_ctx overrides model metadata lookups."""
        token_file = tmp_path / "token"
        token_file.write_text("my-secret-token\n")
        client = OllamaClient(
            base_url="http://caddy:8080",
            model="qwen3:8b",
            capabilities_registry=CapabilitiesRegistry(),
            token_path=str(token_file),
            num_ctx=16384,
        )
        assert client.num_ctx == 16384

    def test_reads_token_from_file(self, tmp_path):
        """Verify token is read from tmpfs file."""
        token_file = tmp_path / "token"
        token_file.write_text("my-secret-token\n")

        client = OllamaClient(
            base_url="http://caddy:8080",
            model="qwen3:8b",
            capabilities_registry=CapabilitiesRegistry(),
            token_path=str(token_file),
        )
        assert client._token == "my-secret-token"

    def test_sets_bearer_header(self, tmp_path):
        """Verify Authorization header is set."""
        token_file = tmp_path / "token"
        token_file.write_text("abc123\n")
        client = OllamaClient(
            base_url="http://caddy:8080",
            model="qwen3:8b",
            capabilities_registry=CapabilitiesRegistry(),
            token_path=str(token_file),
        )
        auth = client._client.headers["authorization"]
        assert auth == "Bearer abc123"

    def test_skips_bearer_header_when_token_file_missing(self, tmp_path):
        """Verify direct Ollama endpoints can run without a token file."""
        missing = tmp_path / "missing-token"
        client = OllamaClient(
            base_url="http://192.168.0.100:11434",
            model="qwen3.5:9b-q4_K_M",
            capabilities_registry=CapabilitiesRegistry(),
            token_path=str(missing),
        )
        assert "authorization" not in client._client.headers
        assert client._token == ""


class TestCheckModel:
    """Tests for check_model method."""

    @pytest.mark.asyncio
    async def test_model_found(self, tmp_path):
        """Verify returns True when model is available."""

        async def handler(request):
            assert request.headers["authorization"] == "Bearer test-bearer-token"
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
        client = make_client(transport, tmp_path)
        assert await client.check_model()
        await client.close()

    @pytest.mark.asyncio
    async def test_model_found_with_latest(self, tmp_path):
        """Verify matches model with :latest suffix."""

        async def handler(request):
            return httpx.Response(
                200,
                json={"models": [{"name": "qwen3:8b:latest"}]},
            )

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)
        assert await client.check_model()
        await client.close()

    @pytest.mark.asyncio
    async def test_model_not_found(self, tmp_path):
        """Verify returns False when model is missing."""

        async def handler(request):
            return httpx.Response(
                200,
                json={"models": [{"name": "other-model:7b"}]},
            )

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)
        assert not await client.check_model()
        await client.close()

    @pytest.mark.asyncio
    async def test_chat_returns_tool_calls(self, tmp_path):
        """Verify Ollama tool calls are surfaced to the orchestrator."""

        async def handler(request):
            if request.url.path == "/api/show":
                return httpx.Response(200, json={})
            body = json.loads(request.content.decode())
            assert body["tools"][0]["function"]["name"] == "web_search"
            return httpx.Response(
                200,
                text="\n".join(
                    [
                        json.dumps(
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "",
                                    "tool_calls": [
                                        {
                                            "function": {
                                                "name": "web_search",
                                                "arguments": {"query": "bitcoin price"},
                                            }
                                        }
                                    ],
                                },
                                "done": False,
                            }
                        ),
                        json.dumps(
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "",
                                },
                                "done": True,
                            },
                        ),
                    ]
                ),
            )

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)
        response = await client.chat(
            [{"role": "user", "content": "Какая цена биткойна?"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the web",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        )
        assert response.tool_calls
        assert response.tool_calls[0].name == "web_search"
        assert response.tool_calls[0].arguments == {"query": "bitcoin price"}
        assert response.raw_message["tool_calls"][0]["function"]["arguments"] == {
            "query": "bitcoin price"
        }
        assert response.usage is not None
        assert response.usage.model == "qwen3:8b"
        await client.close()

    @pytest.mark.asyncio
    async def test_chat_collects_usage_stats_from_final_chunk(self, tmp_path):
        """Verify Ollama final stream chunk is converted to usage statistics."""

        async def handler(request):
            return httpx.Response(
                200,
                text="\n".join(
                    [
                        json.dumps(
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "Привет",
                                },
                                "done": False,
                            }
                        ),
                        json.dumps(
                            {
                                "model": "qwen3.5:35b",
                                "message": {
                                    "role": "assistant",
                                    "content": "",
                                },
                                "prompt_eval_count": 321,
                                "eval_count": 54,
                                "prompt_eval_duration": 450000000,
                                "eval_duration": 1600000000,
                                "total_duration": 2200000000,
                                "done": True,
                            }
                        ),
                    ]
                ),
            )

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)
        client.capabilities_registry = _prebuilt_registry(
            "qwen3:8b",
            ctx=LARGE_TEST_NUM_CTX,
        )
        response = await client.chat(
            [{"role": "user", "content": "Привет"}],
        )
        assert response.text == "Привет"
        assert response.usage is not None
        assert response.usage.prompt_tokens == 321
        assert response.usage.completion_tokens == 54
        assert response.usage.prompt_ms == 450.0
        assert response.usage.generation_ms == 1600.0
        assert response.usage.total_ms == 2200.0
        assert response.usage.model == "qwen3.5:35b"
        assert response.usage.context_total == LARGE_TEST_NUM_CTX
        assert response.usage.context_used == 375
        await client.close()

    @pytest.mark.asyncio
    async def test_chat_separates_reasoning_from_visible_text(self, tmp_path):
        """Verify Ollama streams thinking and content as separate channels.

        Ollama 0.5+ exposes ``message.thinking`` alongside ``message.content``;
        the client must forward each channel as its own SSE event without
        buffering.
        """

        async def handler(request):
            if request.url.path == "/api/show":
                return httpx.Response(200, json={})
            return httpx.Response(
                200,
                text="\n".join(
                    [
                        json.dumps(
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "",
                                    "thinking": "internal plan",
                                },
                                "done": False,
                            }
                        ),
                        json.dumps(
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "Visible answer",
                                    "thinking": "",
                                },
                                "done": True,
                                "prompt_eval_count": 1,
                                "eval_count": 1,
                                "prompt_eval_duration": 1,
                                "eval_duration": 1,
                                "total_duration": 1,
                            }
                        ),
                    ]
                ),
            )

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)
        queue: asyncio.Queue = asyncio.Queue()
        response = await client.chat(
            [{"role": "user", "content": "hello"}],
            sse_queue=queue,
        )
        assert response.text == "Visible answer"
        queued = [queue.get_nowait() for _ in range(queue.qsize())]
        assert {"type": "thinking", "content": "internal plan"} in queued
        assert {"type": "token", "content": "Visible answer"} in queued
        assert {"type": "token", "content": "internal plan"} not in queued
        await client.close()

    @pytest.mark.asyncio
    async def test_chat_sends_resolved_num_ctx_in_options(self, tmp_path):
        """Verify resolved capabilities num_ctx is always forwarded to Ollama."""

        async def handler(request):
            if request.url.path == "/api/show":
                return httpx.Response(200, json={})
            body = json.loads(request.content.decode())
            assert body["options"]["num_ctx"] == 16384
            return httpx.Response(
                200,
                text=json.dumps(
                    {
                        "model": "qwen3:8b",
                        "message": {
                            "role": "assistant",
                            "content": "ok",
                        },
                        "prompt_eval_count": 5,
                        "eval_count": 2,
                        "prompt_eval_duration": 1000000,
                        "eval_duration": 2000000,
                        "total_duration": 3000000,
                        "done": True,
                    }
                ),
            )

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)
        # Replace the prebuilt registry with one carrying the desired ctx.
        client.capabilities_registry = _prebuilt_registry("qwen3:8b", ctx=16384)
        response = await client.chat(
            [{"role": "user", "content": "hello"}],
        )
        assert response.usage is not None
        await client.close()

    @pytest.mark.asyncio
    async def test_chat_converts_multimodal_messages_for_ollama(self, tmp_path):
        """Verify OpenAI-style image inputs are rewritten into Ollama messages."""

        async def handler(request):
            if request.url.path == "/api/show":
                return httpx.Response(200, json={})
            body = json.loads(request.content.decode())
            assert body["messages"][0]["content"] == "describe image"
            assert body["messages"][0]["images"] == ["ZmFrZS1pbWFnZQ=="]
            return httpx.Response(
                200,
                text=json.dumps(
                    {
                        "model": "qwen3:8b",
                        "message": {
                            "role": "assistant",
                            "content": "ok",
                        },
                        "prompt_eval_count": 5,
                        "eval_count": 2,
                        "prompt_eval_duration": 1000000,
                        "eval_duration": 2000000,
                        "total_duration": 3000000,
                        "done": True,
                    }
                ),
            )

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)
        response = await client.chat(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "describe image"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,ZmFrZS1pbWFnZQ=="
                            },
                        },
                    ],
                }
            ],
        )
        assert response.text == "ok"
        await client.close()

    @pytest.mark.asyncio
    async def test_empty_model_list(self, tmp_path):
        """Verify returns False for empty model list."""

        async def handler(request):
            return httpx.Response(200, json={"models": []})

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)
        assert not await client.check_model()
        await client.close()

    @pytest.mark.asyncio
    async def test_connection_error(self, tmp_path):
        """Verify raises OllamaClientError on failure."""

        async def handler(request):
            raise httpx.ConnectError("refused")

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)
        with pytest.raises(OllamaClientError, match="Failed to check"):
            await client.check_model()
        await client.close()

    @pytest.mark.asyncio
    async def test_server_error(self, tmp_path):
        """Verify raises OllamaClientError on 500."""

        async def handler(request):
            return httpx.Response(500, text="error")

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)
        with pytest.raises(OllamaClientError, match="Failed to check"):
            await client.check_model()
        await client.close()


class TestPullModel:
    """Tests for pull_model method."""

    @pytest.mark.asyncio
    async def test_pull_success(self, tmp_path):
        """Verify successful model pull with progress."""
        lines = [
            json.dumps({"status": "pulling manifest"}),
            json.dumps(
                {
                    "status": "downloading",
                    "completed": 500,
                    "total": 1000,
                }
            ),
            json.dumps(
                {
                    "status": "downloading",
                    "completed": 1000,
                    "total": 1000,
                }
            ),
            json.dumps({"status": "success"}),
        ]

        async def handler(request):
            body = json.loads(request.content)
            assert body["name"] == "qwen3:8b"
            content = "\n".join(lines)
            return httpx.Response(200, text=content)

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)
        await client.pull_model()
        await client.close()

    @pytest.mark.asyncio
    async def test_pull_error(self, tmp_path):
        """Verify raises OllamaClientError on failure."""

        async def handler(request):
            return httpx.Response(500, text="pull failed")

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)
        with pytest.raises(OllamaClientError, match="Model pull failed"):
            await client.pull_model()
        await client.close()

    @pytest.mark.asyncio
    async def test_pull_connection_error(self, tmp_path):
        """Verify raises OllamaClientError on connect."""

        async def handler(request):
            raise httpx.ConnectError("refused")

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)
        with pytest.raises(OllamaClientError, match="Model pull failed"):
            await client.pull_model()
        await client.close()

    @pytest.mark.asyncio
    async def test_pull_empty_lines_skipped(self, tmp_path):
        """Verify empty lines in pull stream are skipped."""
        lines = [
            "",
            json.dumps({"status": "success"}),
        ]

        async def handler(request):
            content = "\n".join(lines)
            return httpx.Response(200, text=content)

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)
        await client.pull_model()
        await client.close()


class TestChat:
    """Tests for chat method."""

    @pytest.mark.asyncio
    async def test_returns_complete_text(self, tmp_path):
        """Verify streaming chat returns full text."""
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
            if request.url.path == "/api/show":
                return httpx.Response(200, json={})
            body = json.loads(request.content)
            assert body["model"] == "qwen3:8b"
            assert body["stream"] is True
            assert len(body["messages"]) == 1
            content = "\n".join(lines)
            return httpx.Response(200, text=content)

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)

        result = await client.chat([{"role": "user", "content": "Hi"}])
        assert result.text == "Hello world"
        assert result.llm_response_format is LlmResponseFormat.TEXT
        await client.close()

    @pytest.mark.asyncio
    async def test_structured_mode_returns_typed_content(self, tmp_path):
        """Verify Ollama JSON mode returns parsed structured assistant content."""

        async def handler(request):
            body = json.loads(request.content)
            assert body["stream"] is False
            assert body["format"]["type"] == "object"
            return httpx.Response(
                200,
                json={
                    "model": "qwen3:8b",
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "answer": "Visible answer",
                                "sql": "SELECT 1",
                                "suggestions": ["Next step"],
                            }
                        ),
                        "thinking": "internal plan",
                    },
                    "prompt_eval_count": 3,
                    "eval_count": 2,
                    "prompt_eval_duration": 10,
                    "eval_duration": 20,
                    "total_duration": 30,
                    "done": True,
                },
            )

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)
        client.capabilities_registry = _prebuilt_registry(
            "qwen3:8b",
            response_format=LlmResponseFormat.JSON_SCHEMA,
        )

        result = await client.chat([{"role": "user", "content": "Hi"}])
        assert result.text == "Visible answer"
        assert result.structured_content is not None
        assert result.structured_content.answer == "Visible answer"
        assert result.structured_content.thinking == "internal plan"
        assert result.structured_content.sql == "SELECT 1"
        assert result.structured_content.suggestions == ["Next step"]
        await client.close()

    @pytest.mark.asyncio
    async def test_structured_mode_returns_tool_call_without_final_text(
        self,
        tmp_path,
    ):
        """Verify JSON mode still surfaces tool calls before a final answer."""

        async def handler(request):
            body = json.loads(request.content)
            assert body["stream"] is False
            return httpx.Response(
                200,
                json={
                    "model": "qwen3:8b",
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "thinking": "search first",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "web_search",
                                    "arguments": {"query": "bitcoin price"},
                                }
                            }
                        ],
                    },
                    "prompt_eval_count": 3,
                    "eval_count": 2,
                    "prompt_eval_duration": 10,
                    "eval_duration": 20,
                    "total_duration": 30,
                    "done": True,
                },
            )

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)
        client.capabilities_registry = _prebuilt_registry(
            "qwen3:8b",
            response_format=LlmResponseFormat.JSON_SCHEMA,
        )

        result = await client.chat(
            [{"role": "user", "content": "Find bitcoin price"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the web",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        )
        assert result.text == ""
        assert result.structured_content is None
        assert result.tool_calls[0].name == "web_search"
        assert result.tool_calls[0].arguments == {"query": "bitcoin price"}
        await client.close()

    @pytest.mark.asyncio
    async def test_forwards_to_sse_queue(self, tmp_path):
        """Verify tokens are placed on SSE queue."""
        lines = [
            json.dumps(
                {
                    "message": {"content": "tok1"},
                    "done": False,
                }
            ),
            json.dumps(
                {
                    "message": {"content": "tok2"},
                    "done": True,
                }
            ),
        ]

        async def handler(request):
            if request.url.path == "/api/show":
                return httpx.Response(200, json={})
            content = "\n".join(lines)
            return httpx.Response(200, text=content)

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)
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
    async def test_no_queue_no_error(self, tmp_path):
        """Verify chat works without SSE queue."""
        lines = [
            json.dumps(
                {
                    "message": {"content": "ok"},
                    "done": True,
                }
            ),
        ]

        async def handler(request):
            if request.url.path == "/api/show":
                return httpx.Response(200, json={})
            return httpx.Response(200, text="\n".join(lines))

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)

        result = await client.chat([{"role": "user", "content": "Hi"}])
        assert result.text == "ok"
        await client.close()

    @pytest.mark.asyncio
    async def test_empty_content_skipped(self, tmp_path):
        """Verify chunks with empty content are skipped."""
        lines = [
            json.dumps(
                {
                    "message": {"content": ""},
                    "done": False,
                }
            ),
            json.dumps(
                {
                    "message": {"content": "Hello"},
                    "done": True,
                }
            ),
        ]

        async def handler(request):
            return httpx.Response(200, text="\n".join(lines))

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)

        result = await client.chat([{"role": "user", "content": "Hi"}])
        assert result.text == "Hello"
        await client.close()

    @pytest.mark.asyncio
    async def test_empty_lines_skipped(self, tmp_path):
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
            return httpx.Response(200, text="\n".join(lines))

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)

        result = await client.chat([{"role": "user", "content": "test"}])
        assert result.text == "Hi"
        await client.close()

    @pytest.mark.asyncio
    async def test_chat_error(self, tmp_path):
        """Verify raises OllamaClientError on failure."""

        async def handler(request):
            raise httpx.ConnectError("refused")

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)

        with pytest.raises(OllamaClientError, match="Chat request"):
            await client.chat([{"role": "user", "content": "Hi"}])
        await client.close()

    @pytest.mark.asyncio
    async def test_chat_server_error(self, tmp_path):
        """Verify raises OllamaClientError on 500."""

        async def handler(request):
            return httpx.Response(500, text="error")

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)

        with pytest.raises(OllamaClientError, match="Chat request"):
            await client.chat([{"role": "user", "content": "Hi"}])
        await client.close()


class TestMakeLlmCall:
    """Tests for make_llm_call factory."""

    @pytest.mark.asyncio
    async def test_returns_callable(self, tmp_path):
        """Verify factory returns an async callable."""
        lines = [
            json.dumps(
                {
                    "message": {"content": "response"},
                    "done": True,
                }
            ),
        ]

        async def handler(request):
            return httpx.Response(200, text="\n".join(lines))

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)
        llm_call = make_llm_call(client)

        result = await llm_call([{"role": "user", "content": "Hi"}])
        assert result.text == "response"
        await client.close()

    @pytest.mark.asyncio
    async def test_forwards_to_queue(self, tmp_path):
        """Verify llm_call forwards tokens to queue."""
        lines = [
            json.dumps(
                {
                    "message": {"content": "tok"},
                    "done": True,
                }
            ),
        ]

        async def handler(request):
            return httpx.Response(200, text="\n".join(lines))

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)
        queue: asyncio.Queue = asyncio.Queue()
        llm_call = make_llm_call(client, sse_queue=queue)

        await llm_call([{"role": "user", "content": "Hi"}])
        assert queue.qsize() == 1
        await client.close()

    @pytest.mark.asyncio
    async def test_timeout_raises(self, tmp_path):
        """Verify timeout error on slow response."""

        async def handler(request):
            await asyncio.sleep(10)
            return httpx.Response(200, text="late")

        transport = httpx.MockTransport(handler)
        client = make_client(transport, tmp_path)
        llm_call = make_llm_call(client)

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                llm_call([{"role": "user", "content": "Hi"}]),
                timeout=0.1,
            )
        await client.close()

    def test_timeout_constant(self):
        """Verify LLM call timeout is 5 minutes."""
        assert LLM_CALL_TIMEOUT == 300.0


class TestProviderProperty:
    """Tests for the OllamaClient.provider property."""

    def _make_profile(self) -> ProfileConfig:
        """Create a test ProfileConfig."""
        return ProfileConfig(
            profile=HardwareProfile.PERFORMANCE,
            primary_model="qwen3:14b",
            dialog_recorder_model="qwen3:4b",
            dialog_recorder_mode=DialogRecorderMode.PARALLEL,
            distiller_parallel=False,
        )

    def _make_compute(self) -> ComputeConfig:
        """Create a test ComputeConfig."""
        return ComputeConfig(
            mode=ComputeMode.GPU_ONLY,
            gpu_layers=0,
        )

    def test_provider_returns_ollama_provider(self, tmp_path):
        """Verify provider returns OllamaProvider."""
        token_file = tmp_path / "token"
        token_file.write_text("test-token\n")
        client = OllamaClient(
            base_url="http://caddy:8080",
            dialog_recorder_base_url="http://dialog-recorder:11435",
            model="qwen3:14b",
            capabilities_registry=CapabilitiesRegistry(),
            token_path=str(token_file),
            profile_config=self._make_profile(),
            compute_config=self._make_compute(),
        )
        provider = client.provider
        assert isinstance(provider, OllamaProvider)
        assert provider.base_url == "http://caddy:8080"
        assert provider.dialog_recorder_base_url == "http://dialog-recorder:11435"
        assert provider.primary_model == "qwen3:14b"
        assert provider.dialog_recorder_model == "qwen3:4b"
        assert provider.compute_config.mode == (ComputeMode.GPU_ONLY)

    def test_provider_cached_on_second_access(self, tmp_path):
        """Verify provider is cached after first access."""
        token_file = tmp_path / "token"
        token_file.write_text("test-token\n")
        client = OllamaClient(
            base_url="http://caddy:8080",
            model="qwen3:14b",
            capabilities_registry=CapabilitiesRegistry(),
            token_path=str(token_file),
            profile_config=self._make_profile(),
            compute_config=self._make_compute(),
        )
        first = client.provider
        second = client.provider
        assert first is second

    def test_provider_raises_without_profile(self, tmp_path):
        """Verify error when profile_config is None."""
        token_file = tmp_path / "token"
        token_file.write_text("test-token\n")
        client = OllamaClient(
            base_url="http://caddy:8080",
            model="qwen3:14b",
            capabilities_registry=CapabilitiesRegistry(),
            token_path=str(token_file),
            compute_config=self._make_compute(),
        )
        with pytest.raises(
            OllamaClientError,
            match="profile_config must be set",
        ):
            _ = client.provider

    def test_provider_raises_without_compute(self, tmp_path):
        """Verify error when compute_config is None."""
        token_file = tmp_path / "token"
        token_file.write_text("test-token\n")
        client = OllamaClient(
            base_url="http://caddy:8080",
            model="qwen3:14b",
            capabilities_registry=CapabilitiesRegistry(),
            token_path=str(token_file),
            profile_config=self._make_profile(),
        )
        with pytest.raises(
            OllamaClientError,
            match="compute_config must be set",
        ):
            _ = client.provider
