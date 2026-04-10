"""Tests for Ollama runtime capability detection and KV estimation."""

from __future__ import annotations

import httpx
import pytest
from backend.llm.ollama.capabilities import (
    SOURCE_RUNTIME,
    detect_ollama_model_capabilities,
)


TARGET_MODEL = "qwen3.5:9b"
RUNTIME_CONTEXT = 20480
MODEL_MAX_CONTEXT = 262144
EXPECTED_BYTES_PER_TOKEN = 524288
EXPECTED_RUNTIME_KV_BYTES = RUNTIME_CONTEXT * EXPECTED_BYTES_PER_TOKEN


@pytest.mark.asyncio
async def test_detect_ollama_model_capabilities_uses_runtime_context_from_ps() -> None:
    """Runtime context from ``/api/ps`` must win over ``/api/show`` metadata."""

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/ps":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": TARGET_MODEL,
                            "context_length": RUNTIME_CONTEXT,
                            "size": 9319211648,
                            "size_vram": 9319211648,
                        }
                    ]
                },
            )
        if request.url.path == "/api/show":
            return httpx.Response(
                200,
                json={
                    "model_info": {
                        "general.architecture": "qwen35",
                        "qwen35.block_count": 32,
                        "qwen35.attention.head_count": 16,
                        "qwen35.attention.key_length": 256,
                        "qwen35.attention.value_length": 256,
                        "qwen35.context_length": MODEL_MAX_CONTEXT,
                    }
                },
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://ollama:11434",
    )
    try:
        detected = await detect_ollama_model_capabilities(client, TARGET_MODEL)
    finally:
        await client.aclose()

    assert detected.source == SOURCE_RUNTIME
    assert detected.runtime_context_tokens == RUNTIME_CONTEXT
    assert detected.max_context_tokens == RUNTIME_CONTEXT
    assert detected.model_max_context_tokens == MODEL_MAX_CONTEXT
    assert detected.kv_bytes_per_token == EXPECTED_BYTES_PER_TOKEN
    assert detected.estimated_runtime_kv_bytes == EXPECTED_RUNTIME_KV_BYTES
    assert detected.estimated_runtime_kv_gib == 10.0
