"""Model context window capabilities and detection error.

This module defines the immutable value object that carries the resolved
context window capacity for a single ``(provider, model)`` pair, and the
exception raised when capacity cannot be determined. The value object is
populated either by an explicit user override (env var or UI field) or by
provider-native auto-detection. It is consumed by the chat path, the memory
context builder, and the auxiliary models that need to size their prompts.
There is no fallback constant anywhere in this module.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum


# --- Validation messages ---
ERROR_NON_POSITIVE_TOKENS = (
    "max_context_tokens must be a positive integer, got {value!r}"
)
ERROR_EMPTY_SOURCE = "source must be a non-empty string"
ERROR_EMPTY_PROVIDER = "provider must be a non-empty string"
ERROR_EMPTY_MODEL = "model must be a non-empty string"
ERROR_INVALID_RESPONSE_FORMAT = (
    "llm_response_format must be a LlmResponseFormat value, got {value!r}"
)

# --- Error message template for ContextDetectionError ---
DETECTION_ERROR_TEMPLATE = (
    "Failed to detect context window for provider={provider!r} "
    "model={model!r}; attempted sources: {sources}"
)


class LlmResponseFormat(StrEnum):
    """Supported transport formats for LLM responses."""

    TEXT = "text"
    JSON_OBJECT = "json_object"
    JSON_SCHEMA = "json_schema"


@dataclass(frozen=True)
class ModelCapabilities:
    """Resolved context window capacity for a single provider/model pair.

    Attributes:
        provider: Short identifier of the inference provider, e.g. ``"ollama"``.
        model: Provider-specific model identifier, e.g. ``"qwen3:8b"``.
        max_context_tokens: Maximum number of tokens the provider is willing
            to serve for this model. Always strictly positive.
        source: Identifier of where the value came from. Examples:
            ``"env-override"``, ``"ui-override"``, ``"ollama:/api/ps"``.
        llm_response_format: Preferred response transport mode for this
            provider/model pair. Defaults to plain text.
        detected_at: Monotonic timestamp captured at construction, used for
            diagnostics. Defaults to ``time.monotonic()`` at instantiation.
    """

    provider: str
    model: str
    max_context_tokens: int
    source: str
    llm_response_format: LlmResponseFormat = LlmResponseFormat.TEXT
    detected_at: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        """Validate fields and reject malformed inputs."""
        if not self.provider:
            raise ValueError(ERROR_EMPTY_PROVIDER)
        if not self.model:
            raise ValueError(ERROR_EMPTY_MODEL)
        if not isinstance(self.max_context_tokens, int) or self.max_context_tokens <= 0:
            raise ValueError(
                ERROR_NON_POSITIVE_TOKENS.format(value=self.max_context_tokens)
            )
        if not self.source:
            raise ValueError(ERROR_EMPTY_SOURCE)
        if not isinstance(self.llm_response_format, LlmResponseFormat):
            raise ValueError(
                ERROR_INVALID_RESPONSE_FORMAT.format(
                    value=self.llm_response_format,
                )
            )


class ContextDetectionError(Exception):
    """Raised when the context window for a model cannot be determined.

    Carries enough diagnostic information to identify which provider and
    model failed, which sources were attempted, and what the underlying
    cause was (if any). The error message is composed once at construction
    so it is consistent across logs and user-facing surfaces.

    Attributes:
        provider: Short identifier of the inference provider that was queried.
        model: Provider-specific model identifier that failed detection.
        attempted_sources: Tuple of source identifiers that were tried before
            giving up, in order. Empty tuple is allowed when no source was
            even reachable.
        cause: Optional underlying exception that triggered the failure.
    """

    def __init__(
        self,
        provider: str,
        model: str,
        attempted_sources: tuple[str, ...] = (),
        cause: Exception | None = None,
    ) -> None:
        """Build the error with provider/model context and optional cause."""
        self.provider = provider
        self.model = model
        self.attempted_sources = attempted_sources
        self.cause = cause
        message = DETECTION_ERROR_TEMPLATE.format(
            provider=provider,
            model=model,
            sources=", ".join(attempted_sources) if attempted_sources else "<none>",
        )
        super().__init__(message)
