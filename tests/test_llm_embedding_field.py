"""Tests for embedding fields in LLM config (llm-embedding-field, R2).

The route's field allow-list is the only layer that filters LLM config
keys; both embedding fields must pass it so the provider can read them.
"""

from backend.processes.fastapi_app.routes.llm_providers import (
    LLM_CREDENTIAL_FIELDS,
    _credential_fields,
)


class TestEmbeddingCredentialFields:
    """Embedding fields pass the LLM config route allow-list."""

    def test_both_embedding_fields_in_allowlist(self) -> None:
        """S2.1 — the allow-list carries both embedding keys."""
        assert "embedding_model" in LLM_CREDENTIAL_FIELDS
        assert "embedding_base_url" in LLM_CREDENTIAL_FIELDS

    def test_embedding_fields_survive_extraction(self) -> None:
        """S2.1 — a config body's embedding fields reach the credentials dict."""
        body = {
            "service_type": "ollama",
            "model": "qwen3.5:9b",
            "embedding_model": "nomic-embed-text",
            "embedding_base_url": "http://ollama:11434",
        }
        creds = _credential_fields(body)
        assert creds["embedding_model"] == "nomic-embed-text"
        assert creds["embedding_base_url"] == "http://ollama:11434"

    def test_empty_embedding_fields_dropped(self) -> None:
        """S1.3 — empty embedding fields are not stored (vectors stay off)."""
        creds = _credential_fields(
            {"service_type": "ollama", "model": "qwen3.5:9b", "embedding_model": ""}
        )
        assert "embedding_model" not in creds
