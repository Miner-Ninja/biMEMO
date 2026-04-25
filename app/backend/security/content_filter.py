"""Profile-aware content filter facade.

Exposes ``filter_input`` and ``filter_output`` — the entry points
called by the agent loop, the chat memory pipeline, and the tool
broker. Internally builds a ContentFilter per active security
profile by reading toggle flags and pattern lists from
ProfileResolver and selecting the right pattern subsets from
content_scanning. Filters are cached by profile id and rebuilt
the next time the active profile changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.security.content_scanning import (
    BROAD_CODE_EXECUTION_PATTERN_STRINGS,
    BROAD_EXFILTRATION_PATTERN_STRINGS,
    DANGEROUS_SHELL_PATTERN_STRINGS,
    GENERIC_CREDENTIAL_PATTERNS,
    INTERNAL_PATTERN_STRINGS,
    ROLEPLAY_INJECTION_PATTERN_STRINGS,
    STRICT_CREDENTIAL_PATTERNS,
    STRICT_EXFILTRATION_PATTERN_STRINGS,
    STRICT_INJECTION_PATTERN_STRINGS,
    ContentFilter,
    shannon_entropy,
)
from backend.security.content_scanning_models import FilterResult
from backend.security.profiles.resolver import get_resolver


if TYPE_CHECKING:
    from backend.security.profiles.resolver import ProfileResolver


# --- Catalog keys for input filter toggles ---
_KEY_BLOCK_CODE_EXECUTION = "input_filter.block_code_execution"
_KEY_BLOCK_DANGEROUS_SHELL_ONLY = "input_filter.block_dangerous_shell_only"
_KEY_BLOCK_PROMPT_INJECTION = "input_filter.block_prompt_injection"
_KEY_BLOCK_ROLE_PLAY_PHRASES = "input_filter.block_role_play_phrases"
_KEY_BLOCK_CREDENTIALS_STRICT = "input_filter.block_credentials_strict"
_KEY_BLOCK_CREDENTIALS_GENERIC = "input_filter.block_credentials_generic"
_KEY_BLOCK_PII = "input_filter.block_pii"
_KEY_MAX_MESSAGE_SIZE_BYTES = "input_filter.max_message_size_bytes"
_KEY_CUSTOM_CREDENTIAL_PATTERNS = "input_filter.custom_credential_patterns"
_KEY_CUSTOM_INJECTION_PATTERNS = "input_filter.custom_injection_patterns"

# --- Catalog keys for output filter toggles ---
_KEY_REDACT_CREDENTIALS_IN_OUTPUT = "output_filter.redact_credentials_in_output"
_KEY_REDACT_INTERNAL_PATHS = "output_filter.redact_internal_paths"
_KEY_BLOCK_EXFILTRATION_PATTERNS = "output_filter.block_exfiltration_patterns"
_KEY_BLOCK_EXFILTRATION_STRICT_ONLY = "output_filter.block_exfiltration_strict_only"
_KEY_BLOCK_HIGH_ENTROPY_OUTPUT = "output_filter.block_high_entropy_output"
_KEY_BLOCK_INJECTION_IN_OUTPUT = "output_filter.block_injection_in_output"

# --- Image upload allowlist ---
ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB

# --- Custom credential pattern label ---
_CUSTOM_CREDENTIAL_LABEL = "Custom credential"


# --- Per-profile filter cache (rebuilt when active profile changes) ---
_input_cache: tuple[str | None, ContentFilter] | None = None
_output_cache: tuple[str | None, ContentFilter] | None = None


def _custom_credential_pairs(patterns: list[str]) -> list[tuple[str, str]]:
    """Adapt a flat list of regex strings into ContentFilter credential pairs."""
    return [(pattern, _CUSTOM_CREDENTIAL_LABEL) for pattern in patterns]


def _build_input_filter(resolver: ProfileResolver) -> ContentFilter:
    """Compose a ContentFilter for input scanning under the active profile."""
    credential_patterns: list[tuple[str, str]] = []
    if resolver.get_bool(_KEY_BLOCK_CREDENTIALS_STRICT):
        credential_patterns.extend(STRICT_CREDENTIAL_PATTERNS)
    if resolver.get_bool(_KEY_BLOCK_CREDENTIALS_GENERIC):
        credential_patterns.extend(GENERIC_CREDENTIAL_PATTERNS)
    credential_patterns.extend(
        _custom_credential_pairs(resolver.get_list_str(_KEY_CUSTOM_CREDENTIAL_PATTERNS)),
    )

    injection_patterns: list[str] = []
    if resolver.get_bool(_KEY_BLOCK_PROMPT_INJECTION):
        injection_patterns.extend(STRICT_INJECTION_PATTERN_STRINGS)
    if resolver.get_bool(_KEY_BLOCK_ROLE_PLAY_PHRASES):
        injection_patterns.extend(ROLEPLAY_INJECTION_PATTERN_STRINGS)
    injection_patterns.extend(resolver.get_list_str(_KEY_CUSTOM_INJECTION_PATTERNS))

    code_execution_patterns: list[str] = []
    if resolver.get_bool(_KEY_BLOCK_CODE_EXECUTION):
        code_execution_patterns.extend(BROAD_CODE_EXECUTION_PATTERN_STRINGS)
    if resolver.get_bool(_KEY_BLOCK_DANGEROUS_SHELL_ONLY):
        code_execution_patterns.extend(DANGEROUS_SHELL_PATTERN_STRINGS)

    return ContentFilter(
        credential_patterns=credential_patterns,
        injection_patterns=injection_patterns,
        code_execution_patterns=code_execution_patterns,
        exfiltration_patterns=[],
        max_size=resolver.get_int(_KEY_MAX_MESSAGE_SIZE_BYTES),
        block_pii=resolver.get_bool(_KEY_BLOCK_PII),
    )


def _build_output_filter(resolver: ProfileResolver) -> ContentFilter:
    """Compose a ContentFilter for output scanning under the active profile."""
    credential_patterns: list[tuple[str, str]] = []
    if resolver.get_bool(_KEY_REDACT_CREDENTIALS_IN_OUTPUT):
        credential_patterns.extend(STRICT_CREDENTIAL_PATTERNS)
        credential_patterns.extend(GENERIC_CREDENTIAL_PATTERNS)

    injection_patterns: list[str] = []
    if resolver.get_bool(_KEY_BLOCK_INJECTION_IN_OUTPUT):
        injection_patterns.extend(STRICT_INJECTION_PATTERN_STRINGS)
        injection_patterns.extend(ROLEPLAY_INJECTION_PATTERN_STRINGS)

    internal_patterns: list[str] = []
    if resolver.get_bool(_KEY_REDACT_INTERNAL_PATHS):
        internal_patterns.extend(INTERNAL_PATTERN_STRINGS)

    exfiltration_patterns: list[str] = []
    if resolver.get_bool(_KEY_BLOCK_EXFILTRATION_PATTERNS):
        exfiltration_patterns.extend(BROAD_EXFILTRATION_PATTERN_STRINGS)
    elif resolver.get_bool(_KEY_BLOCK_EXFILTRATION_STRICT_ONLY):
        exfiltration_patterns.extend(STRICT_EXFILTRATION_PATTERN_STRINGS)

    return ContentFilter(
        credential_patterns=credential_patterns,
        injection_patterns=injection_patterns,
        internal_patterns=internal_patterns,
        exfiltration_patterns=exfiltration_patterns,
        block_high_entropy=resolver.get_bool(_KEY_BLOCK_HIGH_ENTROPY_OUTPUT),
    )


def _input_filter() -> ContentFilter:
    """Return the cached input filter for the active profile."""
    global _input_cache
    resolver = get_resolver()
    active_id = resolver.active_profile_id
    if _input_cache is None or _input_cache[0] != active_id:
        _input_cache = (active_id, _build_input_filter(resolver))
    return _input_cache[1]


def _output_filter() -> ContentFilter:
    """Return the cached output filter for the active profile."""
    global _output_cache
    resolver = get_resolver()
    active_id = resolver.active_profile_id
    if _output_cache is None or _output_cache[0] != active_id:
        _output_cache = (active_id, _build_output_filter(resolver))
    return _output_cache[1]


def reset_filter_cache() -> None:
    """Clear the per-profile filter cache (used by tests on teardown)."""
    global _input_cache, _output_cache
    _input_cache = None
    _output_cache = None


def filter_input(text: str) -> FilterResult:
    """Filter incoming text against the active profile's input rules."""
    return _input_filter().scan_input(text)


def filter_output(text: str) -> FilterResult:
    """Filter outgoing text against the active profile's output rules."""
    return _output_filter().scan_output(text)


class ImageValidationError(ValueError):
    """Raised when an image data URL fails validation."""


def _split_data_url(data_url: str) -> tuple[str, str]:
    """Split one data URL into header and encoded body."""
    if not data_url.startswith("data:"):
        raise ImageValidationError("Not a data URL")
    if "," not in data_url:
        raise ImageValidationError("Malformed data URL")
    header, encoded_body = data_url.split(",", 1)
    return header, encoded_body


def _validated_image_mime(header: str) -> str:
    """Return the validated image MIME type from one data-URL header."""
    mime = header.split(";")[0].removeprefix("data:")
    if mime not in ALLOWED_IMAGE_MIMES:
        raise ImageValidationError(
            f"Unsupported image type: {mime}. "
            f"Allowed: {', '.join(sorted(ALLOWED_IMAGE_MIMES))}"
        )
    if ";base64" not in header:
        raise ImageValidationError("Only base64-encoded data URLs are accepted")
    return mime


def _decoded_image_bytes(b64_data: str) -> bytes:
    """Decode one base64 image payload or raise a validation error."""
    import base64

    try:
        return base64.b64decode(b64_data, validate=True)
    except Exception as error:
        raise ImageValidationError("Invalid base64 data") from error


def _validate_image_size(decoded: bytes) -> None:
    """Validate the decoded image size against the configured limit."""
    if len(decoded) > MAX_IMAGE_BYTES:
        raise ImageValidationError(
            f"Image too large: {len(decoded)} bytes (max {MAX_IMAGE_BYTES})"
        )


def validate_image_data_url(data_url: str) -> None:
    """Validate a base64 data URL for image content.

    Checks MIME type, base64 decodability, and decoded size.
    Raises ImageValidationError on failure.
    """
    header, b64_data = _split_data_url(data_url)
    _validated_image_mime(header)
    decoded = _decoded_image_bytes(b64_data)
    _validate_image_size(decoded)


__all__ = [
    "ContentFilter",
    "FilterResult",
    "ImageValidationError",
    "filter_input",
    "filter_output",
    "reset_filter_cache",
    "shannon_entropy",
    "validate_image_data_url",
]
