"""Tests for LLMCallOptions typed construction and tool-round response-format override.

Covers the pattern where LLMCallOptions carries an explicit response_format
override (e.g. JSON_SCHEMA with a custom schema) for a single tool-round call,
ensuring the typed values survive construction and propagation.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from backend.llm.call_options import LLMCallOptions
from backend.llm.capabilities import LlmResponseFormat


# --- Test schema for tool-round override scenarios ---
TOOL_ROUND_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


class TestLLMCallOptionsTypedConstruction:
    """Tests that LLMCallOptions accepts and preserves typed enum values."""

    def test_construction_with_json_schema_format_and_schema(self):
        """Verify JSON_SCHEMA format with a custom schema survives construction."""
        options = LLMCallOptions(
            response_format=LlmResponseFormat.JSON_SCHEMA,
            response_schema=TOOL_ROUND_SCHEMA,
        )
        assert options.response_format is LlmResponseFormat.JSON_SCHEMA
        assert options.response_schema is TOOL_ROUND_SCHEMA

    def test_construction_with_text_format_and_no_schema(self):
        """Verify TEXT format with no schema is a valid override combination."""
        options = LLMCallOptions(response_format=LlmResponseFormat.TEXT)
        assert options.response_format is LlmResponseFormat.TEXT
        assert options.response_schema is None

    def test_construction_with_json_object_format(self):
        """Verify JSON_OBJECT format is stored as the enum value."""
        options = LLMCallOptions(response_format=LlmResponseFormat.JSON_OBJECT)
        assert options.response_format is LlmResponseFormat.JSON_OBJECT

    def test_all_fields_none_when_only_format_set(self):
        """Verify other fields remain None when only response_format is set."""
        options = LLMCallOptions(response_format=LlmResponseFormat.JSON_SCHEMA)
        assert options.response_format is LlmResponseFormat.JSON_SCHEMA
        assert options.response_schema is None
        assert options.tools is None
        assert options.tool_choice is None
        assert options.sse_queue is None

    def test_full_tool_round_options_construction(self):
        """Verify the complete tool-round override bundle constructs correctly."""
        options = LLMCallOptions(
            response_format=LlmResponseFormat.JSON_SCHEMA,
            response_schema=TOOL_ROUND_SCHEMA,
            tools=[],
            tool_choice="none",
        )
        assert options.response_format is LlmResponseFormat.JSON_SCHEMA
        assert options.response_schema is TOOL_ROUND_SCHEMA
        assert options.tools == []
        assert options.tool_choice == "none"
        assert options.sse_queue is None


class TestLLMCallOptionsToolRoundPropagation:
    """Tests that response_format override propagates through the dataclass."""

    def test_response_format_override_preserved_across_access(self):
        """Verify response_format value is stable across multiple reads."""
        options = LLMCallOptions(response_format=LlmResponseFormat.JSON_SCHEMA)
        first = options.response_format
        second = options.response_format
        assert first is LlmResponseFormat.JSON_SCHEMA
        assert second is first

    def test_response_schema_identity_preserved(self):
        """Verify the response_schema dict is the same object, not a copy."""
        schema = {"type": "object", "properties": {}}
        options = LLMCallOptions(response_schema=schema)
        assert options.response_schema is schema

    def test_tool_round_schema_is_serializable(self):
        """Verify the stored response_schema round-trips through JSON."""
        options = LLMCallOptions(
            response_format=LlmResponseFormat.JSON_SCHEMA,
            response_schema=TOOL_ROUND_SCHEMA,
        )
        dumped = json.dumps(options.response_schema)
        loaded = json.loads(dumped)
        assert loaded == TOOL_ROUND_SCHEMA

    def test_text_format_override_with_tools_disabled(self):
        """Verify TEXT format with empty tools list for forced-text calls."""
        options = LLMCallOptions(
            response_format=LlmResponseFormat.TEXT,
            tools=[],
        )
        assert options.response_format is LlmResponseFormat.TEXT
        assert options.tools == []
        assert options.tools is not None

    def test_json_schema_format_with_tool_choice_none(self):
        """Verify JSON_SCHEMA format can be paired with tool_choice none."""
        options = LLMCallOptions(
            response_format=LlmResponseFormat.JSON_SCHEMA,
            response_schema=TOOL_ROUND_SCHEMA,
            tool_choice="none",
        )
        assert options.response_format is LlmResponseFormat.JSON_SCHEMA
        assert options.tool_choice == "none"

    def test_format_override_is_frozen(self):
        """Verify the response_format field cannot be reassigned after construction."""
        options = LLMCallOptions(response_format=LlmResponseFormat.TEXT)
        with pytest.raises(dataclasses.FrozenInstanceError):
            options.response_format = LlmResponseFormat.JSON_SCHEMA  # type: ignore[misc]
