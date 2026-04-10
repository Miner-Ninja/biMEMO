"""Tests for provenance tagging.

Covers tool result, user message, record,
and summary wrapping.
"""

from backend.security.provenance import (
    PROVENANCE_SYSTEM_PROMPT,
    tag_observation,
    tag_reflection,
    tag_tool_result,
    tag_user_message,
)


class TestTagToolResult:
    """Tests for tool result tagging."""

    def test_wraps_content(self):
        """Verify tool result is wrapped."""
        tagged = tag_tool_result("data here", "file_read", "req-123")
        assert "<tool_result" in tagged
        assert 'source="file_read"' in tagged
        assert 'request_id="req-123"' in tagged
        assert "data here" in tagged
        assert "</tool_result>" in tagged


class TestTagUserMessage:
    """Tests for user message tagging."""

    def test_wraps_content(self):
        """Verify user message is wrapped."""
        tagged = tag_user_message("hello", "telegram")
        assert "<user_message" in tagged
        assert 'channel="telegram"' in tagged
        assert "hello" in tagged
        assert "</user_message>" in tagged


class TestTagRecord:
    """Tests for record tagging."""

    def test_wraps_content(self):
        """Verify record is wrapped."""
        tagged = tag_observation("obs text")
        assert tagged == ("<record>obs text</record>")


class TestTagSummary:
    """Tests for summary tagging."""

    def test_wraps_content(self):
        """Verify summary is wrapped."""
        tagged = tag_reflection("ref text")
        assert tagged == ("<summary>ref text</summary>")


class TestProvenancePrompt:
    """Tests for system prompt."""

    def test_prompt_mentions_tags(self):
        """Verify prompt mentions all tag types."""
        assert "tool_result" in PROVENANCE_SYSTEM_PROMPT
        assert "user_message" in PROVENANCE_SYSTEM_PROMPT
        assert "record" in PROVENANCE_SYSTEM_PROMPT
        assert "summary" in PROVENANCE_SYSTEM_PROMPT
