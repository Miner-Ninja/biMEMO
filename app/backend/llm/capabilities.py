"""Model context window capabilities and detection error.

This module defines the immutable value object that carries the resolved
context window capacity for a single ``(provider, model)`` pair, and the
exception raised when capacity cannot be determined. The value object is
populated by an explicit user override, provider-native auto-detection, or a
provider-owned conservative default. It is consumed by the chat path, the
memory context builder, and the auxiliary models that need to size their
prompts.
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


class CapabilitySupport(StrEnum):
    """Whether a model supports a specific capability."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


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
        native_tools: Whether the model natively supports tool calls.
        exact_named_choice: Whether the model supports exact named choice
            (structured output with a named choice field).
        required_native_decision: Whether the model/provider honors a request that
            requires one native function decision from the exposed schemas.
        non_streaming_custom_schema: Whether the model supports a custom
            JSON schema in non-streaming mode.
        detected_at: Monotonic timestamp captured at construction, used for
            diagnostics. Defaults to ``time.monotonic()`` at instantiation.
    """

    provider: str
    model: str
    max_context_tokens: int
    source: str
    llm_response_format: LlmResponseFormat = LlmResponseFormat.TEXT
    detected_at: float = field(default_factory=time.monotonic)
    native_tools: CapabilitySupport = CapabilitySupport.UNKNOWN
    exact_named_choice: CapabilitySupport = CapabilitySupport.UNKNOWN
    required_native_decision: CapabilitySupport = CapabilitySupport.UNKNOWN
    non_streaming_custom_schema: CapabilitySupport = CapabilitySupport.UNKNOWN

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
        if not isinstance(self.native_tools, CapabilitySupport):
            raise ValueError(
                "native_tools must be a CapabilitySupport value, got "
                f"{self.native_tools!r}"
            )
        if not isinstance(self.exact_named_choice, CapabilitySupport):
            raise ValueError(
                "exact_named_choice must be a CapabilitySupport value, got "
                f"{self.exact_named_choice!r}"
            )
        if not isinstance(self.required_native_decision, CapabilitySupport):
            raise ValueError(
                "required_native_decision must be a CapabilitySupport value, got "
                f"{self.required_native_decision!r}"
            )
        if not isinstance(self.non_streaming_custom_schema, CapabilitySupport):
            raise ValueError(
                "non_streaming_custom_schema must be a CapabilitySupport value, got "
                f"{self.non_streaming_custom_schema!r}"
            )

    def matches_identity(self, provider: str, model: str) -> bool:
        """Return whether this value belongs to the requested cache identity."""
        return self.provider == provider and self.model == model


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
