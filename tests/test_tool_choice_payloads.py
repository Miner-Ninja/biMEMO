"""Tool-choice payload tests across provider implementations.

Verifies that each provider constructs the correct ``tool_choice`` field
in the HTTP request body: OpenAI-compatible providers always send ``"auto"``
by default (both streaming and non-streaming), Ollama passes through an
exact tool-choice object when one is provided, and the OllamaProvider
(which lacks tool calling) never includes the field.
"""

from __future__ import annotations

import json

import httpx
import pytest
from backend.llm.call_options import LLMCallOptions
from backend.llm.capabilities import (
    CapabilitySupport,
    LlmResponseFormat,
    ModelCapabilities,
)
from backend.llm.ollama.client import OllamaClient
from backend.llm.openrouter.client import OpenRouterClient


# --- Shared test constants ---
TEST_MODEL = "qwen3:8b"
TEST_BASE_URL = "http://localhost:11434"
TEST_OPENROUTER_URL = "https://openrouter.ai"
TEST_API_KEY = "test-key"
PREBUILT_SOURCE = "test:prebuilt"

_SAMPLE_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web",
        "parameters": {"type": "object"},
    },
}

_EXACT_TOOL_CHOICE = {
    "type": "function",
    "function": {"name": "web_search"},
}


def _prebuilt_registry():
    """Return a CapabilitiesRegistry pre-populated for the test model."""
    from backend.llm.capabilities_registry import CapabilitiesRegistry

    registry = CapabilitiesRegistry()
    registry._cache[("ollama", TEST_MODEL)] = ModelCapabilities(
        provider="ollama",
        model=TEST_MODEL,
        max_context_tokens=32768,
        source=PREBUILT_SOURCE,
        llm_response_format=LlmResponseFormat.TEXT,
        exact_named_choice=CapabilitySupport.SUPPORTED,
    )
    return registry


def _capture_payload(request: httpx.Request) -> dict:
    """Decode and return the JSON body of one captured request."""
    return json.loads(request.content.decode())


# ---------------------------------------------------------------------------
# OpenAI-compatible provider payload tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestOpenAICompatiblePayload:
    """Verify tool_choice values in OpenAI-compatible request payloads."""

    async def test_sse_includes_tool_choice_auto(self):
        """Streaming SSE payloads include tool_choice as ``"auto"`` with tools."""
        captured: dict | None = None

        async def handler(request: httpx.Request):
            nonlocal captured
            captured = _capture_payload(request)
            return httpx.Response(
                200,
                text=('data: {"choices":[{"delta":{"content":"ok"}}]}\ndata: [DONE]\n'),
            )

        transport = httpx.MockTransport(handler)
        client = OpenRouterClient(api_key=TEST_API_KEY, model=TEST_MODEL)
        client._client = httpx.AsyncClient(
            transport=transport,
            base_url=TEST_OPENROUTER_URL,
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        )

        try:
            await client.chat(
                [{"role": "user", "content": "Search"}],
                tools=[_SAMPLE_TOOL],
            )
        finally:
            await client.close()

        assert captured is not None
        assert captured["stream"] is True
        assert captured["tools"] == [_SAMPLE_TOOL]
        assert captured["tool_choice"] == "auto"

    async def test_text_tool_round_includes_tool_choice_auto(self):
        """Text-mode tool rounds include ``tool_choice=auto``."""
        captured: dict | None = None

        async def handler(request: httpx.Request):
            nonlocal captured
            captured = _capture_payload(request)
            return httpx.Response(
                200,
                json={
                    "model": TEST_MODEL,
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                },
            )

        transport = httpx.MockTransport(handler)
        client = OpenRouterClient(api_key=TEST_API_KEY, model=TEST_MODEL)
        client._client = httpx.AsyncClient(
            transport=transport,
            base_url=TEST_OPENROUTER_URL,
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        )

        try:
            await client.chat(
                [{"role": "user", "content": "Search"}],
                tools=[_SAMPLE_TOOL],
                options=LLMCallOptions(response_format=LlmResponseFormat.TEXT),
            )
        finally:
            await client.close()

        assert captured is not None
        assert captured["stream"] is True
        assert captured["tools"] == [_SAMPLE_TOOL]
        assert captured["tool_choice"] == "auto"

    async def test_explicit_tool_choice_overrides_auto(self):
        """An explicit tool_choice in LLMCallOptions replaces the default ``"auto"``."""
        captured: dict | None = None

        async def handler(request: httpx.Request):
            nonlocal captured
            captured = _capture_payload(request)
            return httpx.Response(
                200,
                json={
                    "model": TEST_MODEL,
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                },
            )

        transport = httpx.MockTransport(handler)
        client = OpenRouterClient(api_key=TEST_API_KEY, model=TEST_MODEL)
        client._client = httpx.AsyncClient(
            transport=transport,
            base_url=TEST_OPENROUTER_URL,
            headers={"Authorization": f"Bearer {TEST_API_KEY}"},
        )

        try:
            await client.chat(
                [{"role": "user", "content": "Search"}],
                tools=[_SAMPLE_TOOL],
                options=LLMCallOptions(
                    response_format=LlmResponseFormat.TEXT,
                    tool_choice=_EXACT_TOOL_CHOICE,
                ),
            )
        finally:
            await client.close()

        assert captured is not None
        assert captured["tool_choice"] == _EXACT_TOOL_CHOICE


# ---------------------------------------------------------------------------
# Ollama client payload tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestOllamaClientPayload:
    """Verify tool_choice handling in the Ollama client."""

    async def test_default_tool_choice_is_auto(self, tmp_path):
        """Ollama client defaults to ``"auto"`` when tools are present."""
        captured: dict | None = None

        async def handler(request: httpx.Request):
            nonlocal captured
            if request.url.path == "/api/show":
                return httpx.Response(200, json={})
            captured = _capture_payload(request)
            return httpx.Response(
                200,
                text="\n".join(
                    [
                        json.dumps(
                            {
                                "message": {"role": "assistant", "content": ""},
                                "done": True,
                            }
                        ),
                    ]
                ),
            )

        token_file = tmp_path / "token"
        token_file.write_text("test\n")

        client = OllamaClient(
            base_url=TEST_BASE_URL,
            model=TEST_MODEL,
            token_path=str(token_file),
            capabilities_registry=_prebuilt_registry(),
        )
        client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url=TEST_BASE_URL,
            headers={"Authorization": "Bearer test"},
        )

        try:
            await client.chat(
                [{"role": "user", "content": "Search"}],
                tools=[_SAMPLE_TOOL],
            )
        finally:
            await client.close()

        assert captured is not None
        assert captured["tools"] == [_SAMPLE_TOOL]
        assert captured["tool_choice"] == "auto"

    async def test_exact_tool_choice_passes_through(self, tmp_path):
        """An explicit tool_choice dict is forwarded verbatim in the Ollama payload."""
        captured: dict | None = None

        async def handler(request: httpx.Request):
            nonlocal captured
            if request.url.path == "/api/show":
                return httpx.Response(200, json={})
            captured = _capture_payload(request)
            return httpx.Response(
                200,
                text="\n".join(
                    [
                        json.dumps(
                            {
                                "message": {"role": "assistant", "content": ""},
                                "done": True,
                            }
                        ),
                    ]
                ),
            )

        token_file = tmp_path / "token"
        token_file.write_text("test\n")

        client = OllamaClient(
            base_url=TEST_BASE_URL,
            model=TEST_MODEL,
            token_path=str(token_file),
            capabilities_registry=_prebuilt_registry(),
        )
        client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url=TEST_BASE_URL,
            headers={"Authorization": "Bearer test"},
        )

        try:
            await client.chat(
                [{"role": "user", "content": "Search"}],
                tools=[_SAMPLE_TOOL],
                options=LLMCallOptions(tool_choice=_EXACT_TOOL_CHOICE),
            )
        finally:
            await client.close()

        assert captured is not None
        assert captured["tool_choice"] == _EXACT_TOOL_CHOICE
