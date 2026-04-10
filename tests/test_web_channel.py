"""Tests for Web UI channel endpoints.

Covers security headers, tool status tracking,
and status dict output.
"""

from backend.channels.web import (
    SECURITY_HEADERS,
    ToolStatus,
)


class TestSecurityHeaders:
    """Tests for HTTP security headers."""

    def test_has_csp(self):
        """Verify CSP header present."""
        assert "Content-Security-Policy" in SECURITY_HEADERS

    def test_has_frame_options(self):
        """Verify X-Frame-Options is DENY."""
        assert SECURITY_HEADERS["X-Frame-Options"] == "DENY"

    def test_has_content_type(self):
        """Verify X-Content-Type-Options present."""
        assert SECURITY_HEADERS["X-Content-Type-Options"] == "nosniff"

    def test_has_referrer(self):
        """Verify Referrer-Policy present."""
        assert "Referrer-Policy" in SECURITY_HEADERS


class TestToolStatus:
    """Tests for tool execution status."""

    def test_add_tool(self):
        """Verify tool is added to running list."""
        status = ToolStatus()
        status.add("file_read")
        assert "file_read" in status.running_tools

    def test_remove_tool(self):
        """Verify tool is removed from running list."""
        status = ToolStatus()
        status.add("file_read")
        status.remove("file_read")
        assert "file_read" not in status.running_tools

    def test_add_duplicate_noop(self):
        """Verify adding duplicate is noop."""
        status = ToolStatus()
        status.add("file_read")
        status.add("file_read")
        assert len(status.running_tools) == 1

    def test_remove_unknown_noop(self):
        """Verify removing unknown is noop."""
        status = ToolStatus()
        status.remove("nonexistent")

    def test_to_dict(self):
        """Verify dict output format."""
        status = ToolStatus()
        status.add("a")
        status.add("b")
        d = status.to_dict()
        assert set(d["running"]) == {"a", "b"}
