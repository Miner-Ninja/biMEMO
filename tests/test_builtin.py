"""Tests for built-in tool input models.

Covers valid inputs, extra field rejection,
and field constraints.
"""

import pytest
from pydantic import ValidationError

from backend.tools.builtin import (
    EmailSendInput,
    FileReadInput,
    FileSearchInput,
    FileWriteInput,
    HttpFetchInput,
    SystemStatusInput,
    WebSearchInput,
)


class TestFileReadInput:
    """Tests for file_read input model."""

    def test_valid(self):
        """Verify valid input accepted."""
        inp = FileReadInput(path="/home/user/file.txt")
        assert inp.path == "/home/user/file.txt"

    def test_extra_rejected(self):
        """Verify extra fields rejected."""
        with pytest.raises(ValidationError):
            FileReadInput(path="/file", extra="bad")


class TestFileWriteInput:
    """Tests for file_write input model."""

    def test_valid(self):
        """Verify valid input accepted."""
        inp = FileWriteInput(path="/home/user/file.txt", content="data")
        assert inp.content == "data"

    def test_missing_content(self):
        """Verify missing content rejected."""
        with pytest.raises(ValidationError):
            FileWriteInput(path="/file")


class TestFileSearchInput:
    """Tests for file_search input model."""

    def test_valid(self):
        """Verify valid input accepted."""
        inp = FileSearchInput(directory="/home", pattern="*.txt")
        assert inp.pattern == "*.txt"


class TestWebSearchInput:
    """Tests for web_search input model."""

    def test_valid(self):
        """Verify valid query accepted."""
        inp = WebSearchInput(query="python docs")
        assert inp.query == "python docs"

    def test_query_too_long(self):
        """Verify query over 200 chars rejected."""
        with pytest.raises(ValidationError):
            WebSearchInput(query="x" * 201)


class TestHttpFetchInput:
    """Tests for http_fetch input model."""

    def test_valid(self):
        """Verify valid URL accepted."""
        inp = HttpFetchInput(url="https://example.com")
        assert inp.url == "https://example.com"


class TestEmailSendInput:
    """Tests for email_send input model."""

    def test_valid(self):
        """Verify valid email input accepted."""
        inp = EmailSendInput(to="a@b.com", subject="Hi", body="Hello")
        assert inp.to == "a@b.com"

    def test_missing_field(self):
        """Verify missing field rejected."""
        with pytest.raises(ValidationError):
            EmailSendInput(to="a@b.com", subject="Hi")


class TestNoParamTools:
    """Tests for tools with no parameters."""

    def test_system_status(self):
        """Verify system_status accepts empty."""
        inp = SystemStatusInput()
        assert inp is not None

    def test_extra_rejected(self):
        """Verify extra fields rejected."""
        with pytest.raises(ValidationError):
            SystemStatusInput(extra="bad")
