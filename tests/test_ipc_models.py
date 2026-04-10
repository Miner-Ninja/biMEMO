"""Tests for IPC Pydantic models with strict validation.

Covers request/response serialization, extra field rejection,
and field type validation.
"""

import pytest
from backend.ipc.protocol import ToolRequest, ToolResponse
from pydantic import ValidationError


class TestToolRequest:
    """Tests for ToolRequest model."""

    def test_valid_request(self):
        """Verify valid request fields are accepted."""
        req = ToolRequest(
            request_id="req-1",
            tool_name="file_read",
            parameters={"path": "/data/file.txt"},
        )
        assert req.request_id == "req-1"
        assert req.tool_name == "file_read"

    def test_extra_fields_rejected(self):
        """Verify unexpected fields raise ValidationError."""
        with pytest.raises(ValidationError):
            ToolRequest(
                request_id="req-1",
                tool_name="test",
                parameters={},
                extra_field="bad",
            )

    def test_missing_required_field(self):
        """Verify missing required field raises ValidationError."""
        with pytest.raises(ValidationError):
            ToolRequest(request_id="req-1", tool_name="test")

    def test_json_roundtrip(self):
        """Verify JSON serialization/deserialization roundtrip."""
        req = ToolRequest(
            request_id="req-2",
            tool_name="web_search",
            parameters={"query": "test"},
        )
        json_str = req.model_dump_json()
        restored = ToolRequest.model_validate_json(json_str)
        assert restored == req


class TestToolResponse:
    """Tests for ToolResponse model."""

    def test_success_response(self):
        """Verify success response with result."""
        resp = ToolResponse(
            request_id="req-1",
            status="success",
            result={"data": "content"},
        )
        assert resp.status == "success"
        assert resp.result == {"data": "content"}

    def test_error_response(self):
        """Verify error response with message."""
        resp = ToolResponse(
            request_id="req-1",
            status="error",
            error_message="File not found",
        )
        assert resp.error_message == "File not found"

    def test_extra_fields_rejected(self):
        """Verify unexpected fields raise ValidationError."""
        with pytest.raises(ValidationError):
            ToolResponse(
                request_id="req-1",
                status="success",
                unexpected="bad",
            )
