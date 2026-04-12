"""Tests for the central LLM response-format resolver."""

from __future__ import annotations

from backend.llm.capabilities import LlmResponseFormat
from backend.llm.response_format import (
    EXACT_RESPONSE_FORMAT_OVERRIDES,
    PROVIDER_DEFAULT_RESPONSE_FORMATS,
    PROVIDER_OLLAMA,
    resolve_llm_response_format,
)


# --- Provider and model fixtures ---
OLLAMA_PRIMARY_MODEL = "qwen3:8b"
OLLAMA_TEST_MODEL = "qwen3.5:9b"
OPENROUTER_PROVIDER = "openrouter"
OPENROUTER_MODEL = "gpt-4o"
OPENROUTER_STRUCTURED_MODEL = "openai/gpt-4o"


def test_exact_override_wins_over_provider_default() -> None:
    """Return the exact override before consulting provider defaults."""
    exact_key = (PROVIDER_OLLAMA, OLLAMA_PRIMARY_MODEL)
    original_override = EXACT_RESPONSE_FORMAT_OVERRIDES.get(exact_key)
    EXACT_RESPONSE_FORMAT_OVERRIDES[exact_key] = LlmResponseFormat.JSON_OBJECT
    try:
        resolved = resolve_llm_response_format(
            provider_name=PROVIDER_OLLAMA,
            model_name=OLLAMA_PRIMARY_MODEL,
        )
    finally:
        if original_override is None:
            del EXACT_RESPONSE_FORMAT_OVERRIDES[exact_key]
        else:
            EXACT_RESPONSE_FORMAT_OVERRIDES[exact_key] = original_override
    assert resolved is LlmResponseFormat.JSON_OBJECT


def test_provider_default_used_when_no_exact_override_exists() -> None:
    """Return the provider default when no exact override is configured."""
    resolved = resolve_llm_response_format(
        provider_name=PROVIDER_OLLAMA,
        model_name=OLLAMA_TEST_MODEL,
    )
    assert resolved is PROVIDER_DEFAULT_RESPONSE_FORMATS[PROVIDER_OLLAMA]


def test_text_used_when_provider_has_no_configured_default() -> None:
    """Fall back to plain text when no override or provider default exists."""
    resolved = resolve_llm_response_format(
        provider_name=OPENROUTER_PROVIDER,
        model_name=OPENROUTER_MODEL,
    )
    assert resolved is LlmResponseFormat.TEXT


def test_exact_override_can_enable_structured_openrouter_mode() -> None:
    """Allow one exact OpenRouter model to opt into JSON schema output."""
    exact_key = (OPENROUTER_PROVIDER, OPENROUTER_STRUCTURED_MODEL)
    original_override = EXACT_RESPONSE_FORMAT_OVERRIDES.get(exact_key)
    EXACT_RESPONSE_FORMAT_OVERRIDES[exact_key] = LlmResponseFormat.JSON_SCHEMA
    try:
        resolved = resolve_llm_response_format(
            provider_name=OPENROUTER_PROVIDER,
            model_name=OPENROUTER_STRUCTURED_MODEL,
        )
    finally:
        if original_override is None:
            del EXACT_RESPONSE_FORMAT_OVERRIDES[exact_key]
        else:
            EXACT_RESPONSE_FORMAT_OVERRIDES[exact_key] = original_override
    assert resolved is LlmResponseFormat.JSON_SCHEMA
