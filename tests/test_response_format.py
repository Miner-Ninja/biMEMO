"""Tests for the central LLM response-format resolver."""

from __future__ import annotations

import pytest
from backend.config import reset_settings
from backend.llm.capabilities import LlmResponseFormat
from backend.llm.response_format import (
    EXACT_RESPONSE_FORMAT_OVERRIDES,
    resolve_llm_response_format,
)


# --- Provider and model fixtures ---
PROVIDER_OLLAMA = "ollama"
OLLAMA_PRIMARY_MODEL = "qwen3:8b"
OLLAMA_TEST_MODEL = "qwen3.5:9b"
OPENROUTER_PROVIDER = "openrouter"
OPENROUTER_MODEL = "gpt-4o"
OPENROUTER_STRUCTURED_MODEL = "openai/gpt-4o"


@pytest.fixture(autouse=True)
def _clear_settings_singleton():
    """Ensure every test sees a fresh Settings read from env."""
    reset_settings()
    yield
    reset_settings()


@pytest.fixture
def _restore_exact_overrides():
    """Snapshot EXACT_RESPONSE_FORMAT_OVERRIDES and restore it after the test."""
    saved = EXACT_RESPONSE_FORMAT_OVERRIDES.copy()
    yield
    EXACT_RESPONSE_FORMAT_OVERRIDES.clear()
    EXACT_RESPONSE_FORMAT_OVERRIDES.update(saved)


def test_text_global_returns_text(monkeypatch) -> None:
    """Verify text setting maps to LlmResponseFormat.TEXT."""
    monkeypatch.setenv("BIMEMO_LLM_RESPONSE_FORMAT", "text")
    resolved = resolve_llm_response_format(
        provider_name=PROVIDER_OLLAMA,
        model_name=OLLAMA_TEST_MODEL,
    )
    assert resolved is LlmResponseFormat.TEXT


def test_json_schema_global_returns_json_schema(monkeypatch) -> None:
    """Verify json_schema setting maps to LlmResponseFormat.JSON_SCHEMA."""
    monkeypatch.setenv("BIMEMO_LLM_RESPONSE_FORMAT", "json_schema")
    resolved = resolve_llm_response_format(
        provider_name=PROVIDER_OLLAMA,
        model_name=OLLAMA_TEST_MODEL,
    )
    assert resolved is LlmResponseFormat.JSON_SCHEMA


@pytest.mark.usefixtures("_restore_exact_overrides")
def test_exact_override_wins_over_text_global(monkeypatch) -> None:
    """Return the exact override instead of the global text setting."""
    monkeypatch.setenv("BIMEMO_LLM_RESPONSE_FORMAT", "text")
    EXACT_RESPONSE_FORMAT_OVERRIDES[(PROVIDER_OLLAMA, OLLAMA_PRIMARY_MODEL)] = (
        LlmResponseFormat.JSON_OBJECT
    )
    resolved = resolve_llm_response_format(
        provider_name=PROVIDER_OLLAMA,
        model_name=OLLAMA_PRIMARY_MODEL,
    )
    assert resolved is LlmResponseFormat.JSON_OBJECT


@pytest.mark.usefixtures("_restore_exact_overrides")
def test_exact_override_wins_over_json_schema_global(monkeypatch) -> None:
    """Return the exact override instead of the global json_schema setting."""
    monkeypatch.setenv("BIMEMO_LLM_RESPONSE_FORMAT", "json_schema")
    EXACT_RESPONSE_FORMAT_OVERRIDES[(PROVIDER_OLLAMA, OLLAMA_PRIMARY_MODEL)] = (
        LlmResponseFormat.TEXT
    )
    resolved = resolve_llm_response_format(
        provider_name=PROVIDER_OLLAMA,
        model_name=OLLAMA_PRIMARY_MODEL,
    )
    assert resolved is LlmResponseFormat.TEXT


@pytest.mark.usefixtures("_restore_exact_overrides")
def test_exact_override_does_not_affect_other_models(monkeypatch) -> None:
    """Verify an override for one model does not leak to others."""
    monkeypatch.setenv("BIMEMO_LLM_RESPONSE_FORMAT", "text")
    EXACT_RESPONSE_FORMAT_OVERRIDES[(PROVIDER_OLLAMA, OLLAMA_PRIMARY_MODEL)] = (
        LlmResponseFormat.JSON_SCHEMA
    )
    resolved = resolve_llm_response_format(
        provider_name=OPENROUTER_PROVIDER,
        model_name=OPENROUTER_MODEL,
    )
    assert resolved is LlmResponseFormat.TEXT


@pytest.mark.usefixtures("_restore_exact_overrides")
def test_exact_override_enables_structured_openrouter_mode(monkeypatch) -> None:
    """Allow one exact OpenRouter model to opt into JSON schema output."""
    monkeypatch.setenv("BIMEMO_LLM_RESPONSE_FORMAT", "text")
    EXACT_RESPONSE_FORMAT_OVERRIDES[
        (OPENROUTER_PROVIDER, OPENROUTER_STRUCTURED_MODEL)
    ] = LlmResponseFormat.JSON_SCHEMA
    resolved = resolve_llm_response_format(
        provider_name=OPENROUTER_PROVIDER,
        model_name=OPENROUTER_STRUCTURED_MODEL,
    )
    assert resolved is LlmResponseFormat.JSON_SCHEMA
