"""Tests for OllamaProvider.resolve_capabilities and OllamaClient.resolve_capabilities.

Covers the override-or-detect priority chain: env var override (highest),
UI override (``num_ctx``), provider warmup detection, and the rejection
of zero or negative override values. Mocks the underlying httpx client
with ``httpx.MockTransport`` to keep the chain pure.
"""

from __future__ import annotations

import httpx
import pytest
from backend.llm.capabilities import ContextDetectionError, LlmResponseFormat
from backend.llm.capabilities_registry import CapabilitiesRegistry
from backend.llm.compute_config import ComputeConfig, ComputeMode
from backend.llm.ollama.client import OllamaClient
from backend.llm.ollama.kv_detector import (
    PS_ENDPOINT,
    SOURCE_PS,
)
from backend.llm.ollama.provider import (
    ENV_VAR_NUM_CTX,
    SOURCE_ENV_OVERRIDE,
    SOURCE_UI_OVERRIDE,
    OllamaProvider,
)


# --- Test fixtures ---
TARGET_MODEL = "qwen3:8b"
ENV_OVERRIDE_VALUE = 16384
UI_OVERRIDE_VALUE = 12000
DETECT_VALUE = 32768
BASE_URL = "http://ollama:11434"


def _ps_handler_with(model: str, context_length: int):
    """Build a handler that returns a single loaded model entry."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == PS_ENDPOINT:
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": model, "context_length": context_length},
                    ]
                },
            )
        return httpx.Response(404)

    return handler


def _make_provider(
    *,
    transport: httpx.MockTransport,
    num_ctx: int | None = None,
) -> OllamaProvider:
    """Construct an OllamaProvider wired to a mock transport.

    A bare ``CapabilitiesRegistry`` is injected to satisfy the now-required
    constructor field. ``resolve_capabilities`` does not consult the
    registry, so an empty one is harmless for these tests.
    """
    provider = OllamaProvider(
        base_url=BASE_URL,
        primary_model=TARGET_MODEL,
        dialog_recorder_model=TARGET_MODEL,
        compute_config=ComputeConfig(mode=ComputeMode.GPU_ONLY, gpu_layers=0),
        capabilities_registry=CapabilitiesRegistry(),
        num_ctx=num_ctx,
    )
    provider._client = httpx.AsyncClient(transport=transport, base_url=BASE_URL)
    return provider


def _make_client(
    *,
    transport: httpx.MockTransport,
    num_ctx: int | None = None,
    tmp_path,
) -> OllamaClient:
    """Construct an OllamaClient wired to a mock transport."""
    token_file = tmp_path / "token"
    token_file.write_text("t\n")
    client = OllamaClient(
        base_url=BASE_URL,
        model=TARGET_MODEL,
        capabilities_registry=CapabilitiesRegistry(),
        token_path=str(token_file),
        num_ctx=num_ctx,
    )
    client._client = httpx.AsyncClient(transport=transport, base_url=BASE_URL)
    return client


# --- OllamaProvider.resolve_capabilities ---


@pytest.mark.asyncio
class TestProviderResolveEnvOverride:
    """Env var override always wins."""

    async def test_env_override_wins_over_ui_and_detect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When env is set, neither UI override nor detection is consulted."""
        monkeypatch.setenv(ENV_VAR_NUM_CTX, str(ENV_OVERRIDE_VALUE))

        def handler(request: httpx.Request) -> httpx.Response:
            pytest.fail("HTTP must not be called when env override is set")
            return httpx.Response(500)

        provider = _make_provider(
            transport=httpx.MockTransport(handler),
            num_ctx=UI_OVERRIDE_VALUE,
        )
        try:
            caps = await provider.resolve_capabilities(TARGET_MODEL)
        finally:
            await provider.close()

        assert caps.max_context_tokens == ENV_OVERRIDE_VALUE
        assert caps.source == SOURCE_ENV_OVERRIDE
        assert caps.provider == "ollama"
        assert caps.model == TARGET_MODEL
        assert caps.llm_response_format is LlmResponseFormat.TEXT

    async def test_env_zero_treated_as_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Zero env value is ignored; chain proceeds to UI override."""
        monkeypatch.setenv(ENV_VAR_NUM_CTX, "0")
        provider = _make_provider(
            transport=httpx.MockTransport(_ps_handler_with(TARGET_MODEL, DETECT_VALUE)),
            num_ctx=UI_OVERRIDE_VALUE,
        )
        try:
            caps = await provider.resolve_capabilities(TARGET_MODEL)
        finally:
            await provider.close()
        assert caps.max_context_tokens == UI_OVERRIDE_VALUE
        assert caps.source == SOURCE_UI_OVERRIDE

    async def test_env_negative_treated_as_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Negative env value is ignored."""
        monkeypatch.setenv(ENV_VAR_NUM_CTX, "-100")
        provider = _make_provider(
            transport=httpx.MockTransport(_ps_handler_with(TARGET_MODEL, DETECT_VALUE)),
            num_ctx=None,
        )
        try:
            caps = await provider.resolve_capabilities(TARGET_MODEL)
        finally:
            await provider.close()
        assert caps.max_context_tokens == DETECT_VALUE

    async def test_env_non_integer_treated_as_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-numeric env value is ignored."""
        monkeypatch.setenv(ENV_VAR_NUM_CTX, "not-a-number")
        provider = _make_provider(
            transport=httpx.MockTransport(_ps_handler_with(TARGET_MODEL, DETECT_VALUE)),
            num_ctx=None,
        )
        try:
            caps = await provider.resolve_capabilities(TARGET_MODEL)
        finally:
            await provider.close()
        assert caps.max_context_tokens == DETECT_VALUE

    async def test_env_empty_string_treated_as_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty env value is ignored."""
        monkeypatch.setenv(ENV_VAR_NUM_CTX, "")
        provider = _make_provider(
            transport=httpx.MockTransport(_ps_handler_with(TARGET_MODEL, DETECT_VALUE)),
            num_ctx=None,
        )
        try:
            caps = await provider.resolve_capabilities(TARGET_MODEL)
        finally:
            await provider.close()
        assert caps.max_context_tokens == DETECT_VALUE


@pytest.mark.asyncio
class TestProviderResolveUIOverride:
    """UI override (``num_ctx`` attribute) wins over detection."""

    async def test_ui_override_wins_over_detect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When num_ctx is set, detection is not invoked."""
        monkeypatch.delenv(ENV_VAR_NUM_CTX, raising=False)

        def handler(request: httpx.Request) -> httpx.Response:
            pytest.fail("HTTP must not be called when UI override is set")
            return httpx.Response(500)

        provider = _make_provider(
            transport=httpx.MockTransport(handler),
            num_ctx=UI_OVERRIDE_VALUE,
        )
        try:
            caps = await provider.resolve_capabilities(TARGET_MODEL)
        finally:
            await provider.close()

        assert caps.max_context_tokens == UI_OVERRIDE_VALUE
        assert caps.source == SOURCE_UI_OVERRIDE

    async def test_ui_zero_treated_as_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Zero UI override is ignored; chain proceeds to detection."""
        monkeypatch.delenv(ENV_VAR_NUM_CTX, raising=False)
        provider = _make_provider(
            transport=httpx.MockTransport(_ps_handler_with(TARGET_MODEL, DETECT_VALUE)),
            num_ctx=0,
        )
        try:
            caps = await provider.resolve_capabilities(TARGET_MODEL)
        finally:
            await provider.close()
        assert caps.max_context_tokens == DETECT_VALUE
        assert caps.source == SOURCE_PS
        assert caps.llm_response_format is LlmResponseFormat.TEXT

    async def test_ui_negative_treated_as_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Negative UI override is ignored."""
        monkeypatch.delenv(ENV_VAR_NUM_CTX, raising=False)
        provider = _make_provider(
            transport=httpx.MockTransport(_ps_handler_with(TARGET_MODEL, DETECT_VALUE)),
            num_ctx=-1,
        )
        try:
            caps = await provider.resolve_capabilities(TARGET_MODEL)
        finally:
            await provider.close()
        assert caps.max_context_tokens == DETECT_VALUE


@pytest.mark.asyncio
class TestProviderResolveDetect:
    """Falls through to warmup detection when no overrides are set."""

    async def test_detect_used_when_overrides_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Both overrides absent → detection runs and returns the value."""
        monkeypatch.delenv(ENV_VAR_NUM_CTX, raising=False)
        provider = _make_provider(
            transport=httpx.MockTransport(_ps_handler_with(TARGET_MODEL, DETECT_VALUE)),
            num_ctx=None,
        )
        try:
            caps = await provider.resolve_capabilities(TARGET_MODEL)
        finally:
            await provider.close()
        assert caps.max_context_tokens == DETECT_VALUE
        assert caps.source == SOURCE_PS

    async def test_detect_failure_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ContextDetectionError from detection is re-raised by the provider."""
        monkeypatch.delenv(ENV_VAR_NUM_CTX, raising=False)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        provider = _make_provider(
            transport=httpx.MockTransport(handler),
            num_ctx=None,
        )
        try:
            with pytest.raises(ContextDetectionError):
                await provider.resolve_capabilities(TARGET_MODEL)
        finally:
            await provider.close()


# --- OllamaClient.resolve_capabilities ---


@pytest.mark.asyncio
class TestOllamaClientResolveCapabilities:
    """OllamaClient mirrors the same priority chain as OllamaProvider."""

    async def test_env_override_wins(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """Env override is honored on the client wrapper too."""
        monkeypatch.setenv(ENV_VAR_NUM_CTX, str(ENV_OVERRIDE_VALUE))

        def handler(request: httpx.Request) -> httpx.Response:
            pytest.fail("HTTP must not be called when env override is set")
            return httpx.Response(500)

        client = _make_client(
            transport=httpx.MockTransport(handler),
            num_ctx=UI_OVERRIDE_VALUE,
            tmp_path=tmp_path,
        )
        try:
            caps = await client.resolve_capabilities(TARGET_MODEL)
        finally:
            await client.close()
        assert caps.max_context_tokens == ENV_OVERRIDE_VALUE
        assert caps.source == SOURCE_ENV_OVERRIDE
        assert caps.llm_response_format is LlmResponseFormat.TEXT

    async def test_ui_override_wins_over_detect(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """UI override is honored when env is unset."""
        monkeypatch.delenv(ENV_VAR_NUM_CTX, raising=False)

        def handler(request: httpx.Request) -> httpx.Response:
            pytest.fail("HTTP must not be called when UI override is set")
            return httpx.Response(500)

        client = _make_client(
            transport=httpx.MockTransport(handler),
            num_ctx=UI_OVERRIDE_VALUE,
            tmp_path=tmp_path,
        )
        try:
            caps = await client.resolve_capabilities(TARGET_MODEL)
        finally:
            await client.close()
        assert caps.max_context_tokens == UI_OVERRIDE_VALUE
        assert caps.source == SOURCE_UI_OVERRIDE

    async def test_detect_used_when_overrides_unset(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        """Detection is invoked through the client's own httpx instance."""
        monkeypatch.delenv(ENV_VAR_NUM_CTX, raising=False)
        client = _make_client(
            transport=httpx.MockTransport(_ps_handler_with(TARGET_MODEL, DETECT_VALUE)),
            num_ctx=None,
            tmp_path=tmp_path,
        )
        try:
            caps = await client.resolve_capabilities(TARGET_MODEL)
        finally:
            await client.close()
        assert caps.max_context_tokens == DETECT_VALUE
        assert caps.source == SOURCE_PS

    async def test_class_var_name_is_ollama(self) -> None:
        """The provider name attribute is the literal 'ollama'."""
        assert OllamaClient.name == "ollama"
        assert OllamaProvider.name == "ollama"
