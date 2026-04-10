"""Content filter for input/output security scanning.

Provides function-style access to ContentFilter via
a module-level singleton instance.
"""

from backend.security.content_scanning import (
    ContentFilter,
    shannon_entropy,
)
from backend.security.content_scanning_models import FilterResult


# Create a singleton instance for function-style access
_default_filter = ContentFilter()


def _filter() -> ContentFilter:
    """Return the module-level content filter singleton."""
    return _default_filter


def filter_input(text: str, max_size: int = 100000) -> FilterResult:
    """Filter incoming text (user messages, tool results)."""
    return _filter().scan_input(text)


def filter_output(text: str) -> FilterResult:
    """Filter outgoing text (LLM responses, tool params)."""
    return _filter().scan_output(text)


ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB


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
    "shannon_entropy",
    "validate_image_data_url",
]
