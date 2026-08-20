"""Tests for the LLMCallOptions per-call override value type.

Covers default construction, field propagation, and immutability of the
frozen dataclass declared in backend/llm/call_options.py.
"""

import asyncio

import pytest

from backend.llm.call_options import LLMCallOptions
from backend.llm.capabilities import LlmResponseFormat


class TestLLMCallOptionsDefaults:
    """Tests that every field defaults to None when constructed empty."""

    def test_default_response_format_is_none(self):
        """Verify response_format defaults to None (use default)."""
        options = LLMCallOptions()
        assert options.response_format is None

    def test_default_response_schema_is_none(self):
        """Verify response_schema defaults to None (use default)."""
        options = LLMCallOptions()
        assert options.response_schema is None

    def test_default_tools_is_none(self):
        """Verify tools defaults to None (use default)."""
        options = LLMCallOptions()
        assert options.tools is None

    def test_default_tool_choice_is_none(self):
        """Verify tool_choice defaults to None (use default)."""
        options = LLMCallOptions()
        assert options.tool_choice is None

    def test_default_stream_queue_is_none(self):
        """Verify stream_queue defaults to None (use default)."""
        options = LLMCallOptions()
        assert options.stream_queue is None


class TestLLMCallOptionsFields:
    """Tests that explicitly set fields survive construction."""

    def test_response_format_text_is_stored(self):
        """Verify response_format TEXT is stored as-is."""
        options = LLMCallOptions(response_format=LlmResponseFormat.TEXT)
        assert options.response_format is LlmResponseFormat.TEXT

    def test_response_format_json_schema_is_stored(self):
        """Verify response_format JSON_SCHEMA is stored as-is."""
        options = LLMCallOptions(response_format=LlmResponseFormat.JSON_SCHEMA)
        assert options.response_format is LlmResponseFormat.JSON_SCHEMA

    def test_response_schema_dict_is_stored(self):
        """Verify a JSON schema dict is stored as-is."""
        schema = {"type": "object", "properties": {}, "required": []}
        options = LLMCallOptions(response_schema=schema)
        assert options.response_schema is schema

    def test_tools_list_is_stored(self):
        """Verify a tools list is stored as-is."""
        tools = [{"type": "function", "function": {"name": "t"}}]
        options = LLMCallOptions(tools=tools)
        assert options.tools is tools

    def test_empty_tools_list_is_distinct_from_none(self):
        """Verify an empty tools list is preserved (means 'no tools')."""
        options = LLMCallOptions(tools=[])
        assert options.tools == []
        assert options.tools is not None

    def test_tool_choice_string_is_stored(self):
        """Verify a tool_choice string value is stored."""
        options = LLMCallOptions(tool_choice="none")
        assert options.tool_choice == "none"

    def test_stream_queue_instance_is_stored(self):
        """Verify an asyncio.Queue instance is stored by reference."""
        queue: asyncio.Queue = asyncio.Queue()
        options = LLMCallOptions(stream_queue=queue)
        assert options.stream_queue is queue


class TestLLMCallOptionsImmutable:
    """Tests that LLMCallOptions is a frozen dataclass."""

    def test_cannot_reassign_field(self):
        """Verify attempting to reassign a field raises FrozenInstanceError."""
        import dataclasses

        options = LLMCallOptions()
        with pytest.raises(dataclasses.FrozenInstanceError):
            options.response_format = LlmResponseFormat.TEXT  # type: ignore[misc]
