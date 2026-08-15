"""Tests for the embedding connectivity probe (llm-config-tabs, S2).

The Test Connection button on the vectors tab must run a real
embedding request, not a chat probe against the chat host.
"""

import pytest
from backend.processes.fastapi_app.routes.llm_providers import (
    _probe_embedding,
    _probe_llm_provider,
    _provider_probe_values,
)


class TestProbeValues:
    """The raw request body carries the probe kind through."""

    def test_probe_defaults_to_chat(self) -> None:
        """Bodies without a probe field keep the old chat behaviour."""
        values = _provider_probe_values({"service_type": "custom"})
        assert values["probe"] == "chat"

    def test_probe_embedding_passes_through(self) -> None:
        """An embedding probe request reaches the prober."""
        values = _provider_probe_values(
            {"service_type": "custom", "probe": "embedding"}
        )
        assert values["probe"] == "embedding"


class TestEmbeddingProbe:
    """The embedding probe validates input before any network call."""

    @pytest.mark.asyncio
    async def test_missing_model_fails_cleanly(self) -> None:
        """No model means no request — a clear error comes back."""
        result = await _probe_embedding(
            service_type="custom",
            api_key="",
            model="",
            base_url="http://example.invalid",
        )
        assert result["ok"] is False
        assert "model" in result["error"]

    @pytest.mark.asyncio
    async def test_unreachable_host_reports_error(self) -> None:
        """A dead host yields ok=False with a message, not an exception."""
        result = await _probe_llm_provider(
            service_type="custom",
            api_key="",
            model="some-model",
            base_url="http://127.0.0.1:1",
            probe="embedding",
        )
        assert result["ok"] is False
        assert result["error"]
