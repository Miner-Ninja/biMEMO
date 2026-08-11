"""Tests for lenient structured-JSON parsing (spurious leading brace repair).

Some structured-output backends (observed with vLLM guided decoding on a
quantized model) prepend an extra opening brace to the JSON object, e.g.
``{{"answer": ...}`` or ``{"{ "answer": ...}``. Such replies are invalid JSON
and must be repaired rather than crash the chat handler, while well-formed and
truly broken replies keep their existing behaviour.
"""

import json

import pytest

from backend.llm.structured_response import parse_structured_response_json


class TestLeadingBraceRepair:
    """A spurious leading brace is repaired; valid and garbage are untouched."""

    def test_doubled_opening_brace_is_repaired(self) -> None:
        """A '{{...}' reply parses to the real object."""
        raw = '{{"answer": "Hello!"\n   , "suggestions": []\n   }'
        result = parse_structured_response_json(raw)
        assert result.answer == "Hello!"

    def test_brace_quote_prefix_is_repaired(self) -> None:
        """A '{"{ ...}' reply parses to the real object."""
        raw = '{"{\n   "answer": "Hello!"\n  , "suggestions": []\n}'
        result = parse_structured_response_json(raw)
        assert result.answer == "Hello!"

    def test_valid_json_is_unchanged(self) -> None:
        """A well-formed reply parses without any repair."""
        raw = '{"answer": "ok", "suggestions": []}'
        result = parse_structured_response_json(raw)
        assert result.answer == "ok"

    def test_unrepairable_garbage_still_raises(self) -> None:
        """Text with no recoverable JSON object still raises."""
        with pytest.raises(json.JSONDecodeError):
            parse_structured_response_json("not json at all {oops")
