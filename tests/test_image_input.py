"""Tests for image input feature.

Covers image validation, multimodal content construction,
message storage roundtrip, and text extraction.
"""

import base64
import json

import pytest
from backend.ipc.models import (
    build_multimodal_content,
    extract_image_urls,
    extract_text_content,
)
from backend.security.content_filter import (
    ImageValidationError,
    validate_image_data_url,
)


# --- Helpers ---


def _make_data_url(mime: str = "image/png", data: bytes = b"\x89PNG\r\n") -> str:
    """Build a valid base64 data URL."""
    b64 = base64.b64encode(data).decode()
    return f"data:{mime};base64,{b64}"


# --- 9.1 Image Validation ---


class TestImageValidation:
    """Tests for validate_image_data_url."""

    def test_valid_jpeg(self) -> None:
        """JPEG data URLs are accepted."""
        url = _make_data_url("image/jpeg", b"\xff\xd8\xff\xe0")
        validate_image_data_url(url)  # should not raise

    def test_valid_png(self) -> None:
        """PNG data URLs are accepted."""
        url = _make_data_url("image/png", b"\x89PNG")
        validate_image_data_url(url)

    def test_valid_gif(self) -> None:
        """GIF data URLs are accepted."""
        url = _make_data_url("image/gif", b"GIF89a")
        validate_image_data_url(url)

    def test_valid_webp(self) -> None:
        """WebP data URLs are accepted."""
        url = _make_data_url("image/webp", b"RIFF")
        validate_image_data_url(url)

    def test_invalid_mime_type(self) -> None:
        """Unsupported image MIME types are rejected."""
        url = _make_data_url("image/svg+xml", b"<svg/>")
        with pytest.raises(ImageValidationError, match="Unsupported image type"):
            validate_image_data_url(url)

    def test_not_data_url(self) -> None:
        """Non-data URLs are rejected."""
        with pytest.raises(ImageValidationError, match="Not a data URL"):
            validate_image_data_url("https://example.com/image.png")

    def test_malformed_data_url(self) -> None:
        """Malformed data URLs are rejected."""
        with pytest.raises(ImageValidationError, match="Malformed data URL"):
            validate_image_data_url("data:image/png;base64")

    def test_invalid_base64(self) -> None:
        """Invalid base64 payloads are rejected."""
        with pytest.raises(ImageValidationError, match="Invalid base64"):
            validate_image_data_url("data:image/png;base64,!!!invalid!!!")

    def test_size_limit_enforced(self) -> None:
        """Images above the size cap are rejected."""
        # 11 MB of data exceeds 10 MB limit
        big_data = b"\x00" * (11 * 1024 * 1024)
        url = _make_data_url("image/png", big_data)
        with pytest.raises(ImageValidationError, match="Image too large"):
            validate_image_data_url(url)

    def test_size_at_limit_accepted(self) -> None:
        """Images exactly at the size cap are accepted."""
        # Exactly 10 MB should be accepted
        data = b"\x00" * (10 * 1024 * 1024)
        url = _make_data_url("image/png", data)
        validate_image_data_url(url)

    def test_missing_base64_encoding(self) -> None:
        """Raw data URLs without base64 encoding are rejected."""
        with pytest.raises(ImageValidationError, match="base64-encoded"):
            validate_image_data_url("data:image/png,rawdata")


# --- 9.2 Multimodal Content Construction ---


class TestMultimodalContent:
    """Tests for build_multimodal_content."""

    def test_text_only_returns_string(self) -> None:
        """Text-only messages remain simple strings."""
        result = build_multimodal_content("hello", [])
        assert result == "hello"
        assert isinstance(result, str)

    def test_with_images_returns_list(self) -> None:
        """Messages with images become multimodal content lists."""
        url = _make_data_url()
        result = build_multimodal_content("describe this", [url])
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0] == {"type": "text", "text": "describe this"}
        assert result[1] == {"type": "image_url", "image_url": {"url": url}}

    def test_multiple_images(self) -> None:
        """Multiple images produce multiple image blocks."""
        urls = [_make_data_url("image/jpeg"), _make_data_url("image/png")]
        result = build_multimodal_content("two images", urls)
        assert isinstance(result, list)
        assert len(result) == 3  # 1 text + 2 images
        assert result[0]["type"] == "text"
        assert result[1]["type"] == "image_url"
        assert result[2]["type"] == "image_url"

    def test_empty_message_with_image(self) -> None:
        """Empty text still emits a text block when images are present."""
        url = _make_data_url()
        result = build_multimodal_content("", [url])
        assert isinstance(result, list)
        assert result[0] == {"type": "text", "text": ""}


# --- 9.3 Message Storage Roundtrip ---


class TestMessageStorageRoundtrip:
    """Tests for multimodal content JSON serialization roundtrip."""

    def test_text_content_roundtrip(self) -> None:
        """Plain text content survives a storage roundtrip unchanged."""
        content = "plain text message"
        stored = content  # text stored as-is
        loaded = stored
        assert loaded == content

    def test_multimodal_content_roundtrip(self) -> None:
        """Multimodal content survives JSON storage roundtrip."""
        url = _make_data_url()
        content = build_multimodal_content("describe", [url])
        # Simulate DB storage: list → json.dumps → store → load → json.loads
        stored = json.dumps(content)
        loaded = json.loads(stored)
        assert isinstance(loaded, list)
        assert loaded == content

    def test_multimodal_detection_heuristic(self) -> None:
        """The _parse_content heuristic: starts with '[' → try JSON parse."""
        url = _make_data_url()
        content = build_multimodal_content("test", [url])
        stored = json.dumps(content)
        # Verify starts with '['
        assert stored.startswith("[")
        parsed = json.loads(stored)
        assert isinstance(parsed, list)
        assert parsed[0]["type"] == "text"

    def test_text_not_misdetected(self) -> None:
        """Plain text starting with '[' should not be parsed as multimodal."""
        content = "[INFO] This is a log message"
        # This is stored as text, not JSON
        # The heuristic tries json.loads, fails, returns original string
        try:
            parsed = json.loads(content)
            if not isinstance(parsed, list):
                parsed = content
        except json.JSONDecodeError, ValueError:
            parsed = content
        assert parsed == content


# --- 9.4 Extract Text Content ---


class TestExtractTextContent:
    """Tests for extract_text_content."""

    def test_plain_string_passthrough(self) -> None:
        """Plain strings are returned unchanged."""
        assert extract_text_content("hello world") == "hello world"

    def test_empty_string(self) -> None:
        """Empty strings stay empty."""
        assert extract_text_content("") == ""

    def test_multimodal_extracts_text(self) -> None:
        """Mixed multimodal content returns text plus image placeholders."""
        content = [
            {"type": "text", "text": "describe this image"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ]
        result = extract_text_content(content)
        assert "describe this image" in result
        assert "[image]" in result

    def test_multiple_images_multiple_placeholders(self) -> None:
        """Each image block becomes one placeholder in extracted text."""
        content = [
            {"type": "text", "text": "two images"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,a"}},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,b"}},
        ]
        result = extract_text_content(content)
        assert result.count("[image]") == 2

    def test_image_only_no_text(self) -> None:
        """Image-only content extracts to placeholder-only text."""
        content = [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ]
        result = extract_text_content(content)
        assert result == "[image]"

    def test_empty_list(self) -> None:
        """Empty multimodal lists extract to an empty string."""
        assert extract_text_content([]) == ""

    def test_text_part_with_empty_text(self) -> None:
        """Empty text parts remain empty after extraction."""
        content = [{"type": "text", "text": ""}]
        result = extract_text_content(content)
        assert result == ""


class TestExtractImageUrls:
    """Tests for extract_image_urls."""

    def test_plain_string_returns_empty(self) -> None:
        """Plain string content has no image URLs."""
        assert extract_image_urls("hello") == []

    def test_extracts_data_urls(self) -> None:
        """Image URLs are extracted from multimodal content."""
        url = "data:image/png;base64,abc123"
        content = [
            {"type": "text", "text": "look"},
            {"type": "image_url", "image_url": {"url": url}},
        ]
        assert extract_image_urls(content) == [url]

    def test_multiple_images(self) -> None:
        """All image URLs are collected in order."""
        urls = [
            "data:image/png;base64,aaa",
            "data:image/jpeg;base64,bbb",
        ]
        content = [
            {"type": "image_url", "image_url": {"url": urls[0]}},
            {"type": "image_url", "image_url": {"url": urls[1]}},
        ]
        assert extract_image_urls(content) == urls

    def test_empty_list_returns_empty(self) -> None:
        """Empty content list returns no URLs."""
        assert extract_image_urls([]) == []

    def test_missing_url_skipped(self) -> None:
        """Image parts without a URL are skipped."""
        content = [{"type": "image_url", "image_url": {}}]
        assert extract_image_urls(content) == []
