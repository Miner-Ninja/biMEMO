"""Tests for ModelCapabilities value object and ContextDetectionError.

Covers construction validation (positive token count, non-empty strings),
immutability of the frozen dataclass, error message formatting, and the
exception cause chain. These tests pin down the contract that the rest of
the kv-cache-detection feature relies on, so any future change to the
value object's invariants must update them here first.
"""

from __future__ import annotations

import dataclasses

import pytest
from backend.llm.capabilities import (
    DETECTION_ERROR_TEMPLATE,
    ContextDetectionError,
    ModelCapabilities,
)


# --- Test fixtures: minimal valid argument set ---
VALID_PROVIDER = "ollama"
VALID_MODEL = "qwen3:8b"
VALID_TOKENS = 32768
VALID_SOURCE = "ollama:/api/ps"


def _make_caps(**overrides: object) -> ModelCapabilities:
    """Build a ModelCapabilities with valid defaults overridable by kwargs."""
    base = {
        "provider": VALID_PROVIDER,
        "model": VALID_MODEL,
        "max_context_tokens": VALID_TOKENS,
        "source": VALID_SOURCE,
    }
    base.update(overrides)
    return ModelCapabilities(**base)  # type: ignore[arg-type]


class TestModelCapabilitiesConstruction:
    """Construction and validation tests for ModelCapabilities."""

    def test_valid_construction_succeeds(self) -> None:
        """A fully valid argument set produces a usable instance."""
        caps = _make_caps()
        assert caps.provider == VALID_PROVIDER
        assert caps.model == VALID_MODEL
        assert caps.max_context_tokens == VALID_TOKENS
        assert caps.source == VALID_SOURCE
        assert caps.detected_at > 0

    def test_detected_at_defaults_to_monotonic(self) -> None:
        """detected_at is set to a positive monotonic timestamp by default."""
        caps_a = _make_caps()
        caps_b = _make_caps()
        assert caps_b.detected_at >= caps_a.detected_at

    def test_explicit_detected_at_is_preserved(self) -> None:
        """An explicitly passed detected_at value is not overwritten."""
        caps = _make_caps(detected_at=123.456)
        assert caps.detected_at == 123.456

    def test_zero_max_context_raises(self) -> None:
        """max_context_tokens of zero is rejected."""
        with pytest.raises(ValueError, match="max_context_tokens"):
            _make_caps(max_context_tokens=0)

    def test_negative_max_context_raises(self) -> None:
        """Negative max_context_tokens is rejected."""
        with pytest.raises(ValueError, match="max_context_tokens"):
            _make_caps(max_context_tokens=-1)

    def test_non_integer_max_context_raises(self) -> None:
        """Non-integer max_context_tokens (e.g. float) is rejected."""
        with pytest.raises(ValueError, match="max_context_tokens"):
            _make_caps(max_context_tokens=8192.0)  # type: ignore[arg-type]

    def test_empty_provider_raises(self) -> None:
        """Empty provider string is rejected."""
        with pytest.raises(ValueError, match="provider"):
            _make_caps(provider="")

    def test_empty_model_raises(self) -> None:
        """Empty model string is rejected."""
        with pytest.raises(ValueError, match="model"):
            _make_caps(model="")

    def test_empty_source_raises(self) -> None:
        """Empty source string is rejected."""
        with pytest.raises(ValueError, match="source"):
            _make_caps(source="")


class TestModelCapabilitiesImmutability:
    """Frozen dataclass invariants."""

    def test_cannot_mutate_max_context_tokens(self) -> None:
        """Frozen dataclass forbids assignment after construction."""
        caps = _make_caps()
        with pytest.raises(dataclasses.FrozenInstanceError):
            caps.max_context_tokens = 1024  # type: ignore[misc]

    def test_cannot_mutate_source(self) -> None:
        """Frozen dataclass forbids source mutation."""
        caps = _make_caps()
        with pytest.raises(dataclasses.FrozenInstanceError):
            caps.source = "tampered"  # type: ignore[misc]


class TestContextDetectionError:
    """Diagnostic carriage and message formatting for ContextDetectionError."""

    def test_carries_provider_and_model(self) -> None:
        """Provider and model fields are stored on the exception."""
        error = ContextDetectionError(provider="ollama", model="qwen3:8b")
        assert error.provider == "ollama"
        assert error.model == "qwen3:8b"

    def test_default_attempted_sources_is_empty_tuple(self) -> None:
        """When no sources are passed, attempted_sources is an empty tuple."""
        error = ContextDetectionError(provider="ollama", model="qwen3:8b")
        assert error.attempted_sources == ()

    def test_attempted_sources_are_preserved(self) -> None:
        """Passed sources are stored verbatim."""
        sources = ("env-override", "ollama:/api/ps")
        error = ContextDetectionError(
            provider="ollama",
            model="qwen3:8b",
            attempted_sources=sources,
        )
        assert error.attempted_sources == sources

    def test_message_includes_provider_model_and_sources(self) -> None:
        """The composed error message names the provider, model, and sources."""
        error = ContextDetectionError(
            provider="ollama",
            model="qwen3:8b",
            attempted_sources=("env-override", "ollama:/api/ps"),
        )
        message = str(error)
        assert "ollama" in message
        assert "qwen3:8b" in message
        assert "env-override" in message
        assert "ollama:/api/ps" in message

    def test_message_handles_empty_sources(self) -> None:
        """Empty attempted_sources renders as a placeholder, not blank."""
        error = ContextDetectionError(provider="ollama", model="qwen3:8b")
        assert "<none>" in str(error)

    def test_message_template_used(self) -> None:
        """Sanity check that the documented template is the one in use."""
        rendered = DETECTION_ERROR_TEMPLATE.format(
            provider="ollama",
            model="qwen3:8b",
            sources="env-override",
        )
        error = ContextDetectionError(
            provider="ollama",
            model="qwen3:8b",
            attempted_sources=("env-override",),
        )
        assert str(error) == rendered

    def test_cause_is_stored(self) -> None:
        """The cause exception is preserved on the instance."""
        cause = RuntimeError("ollama down")
        error = ContextDetectionError(
            provider="ollama",
            model="qwen3:8b",
            cause=cause,
        )
        assert error.cause is cause

    def test_default_cause_is_none(self) -> None:
        """Cause defaults to None when omitted."""
        error = ContextDetectionError(provider="ollama", model="qwen3:8b")
        assert error.cause is None

    def test_raise_from_chain(self) -> None:
        """`raise ... from cause` preserves Python's __cause__ chain."""
        original = RuntimeError("network unreachable")
        wrapped = ContextDetectionError(
            provider="ollama",
            model="qwen3:8b",
            cause=original,
        )
        wrapped.__cause__ = original
        with pytest.raises(ContextDetectionError) as exc_info:
            raise wrapped
        assert exc_info.value.__cause__ is original
        assert exc_info.value.cause is original
