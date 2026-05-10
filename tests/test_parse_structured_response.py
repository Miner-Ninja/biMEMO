"""Tests for the structured-response parser defensive guard.

Covers parse_structured_response_json behaviour on empty and
whitespace-only inputs, which previously raised JSONDecodeError and
crashed the client.
"""

from backend.llm.structured_response import (
    StructuredAssistantContent,
    parse_structured_response_json,
)


class TestParseStructuredResponseJsonEmptyGuard:
    """Guard against empty/whitespace input that previously crashed the parser."""

    def test_empty_string_returns_empty_content(self):
        """Verify parse returns empty content, not raises, on empty string."""
        result = parse_structured_response_json("")
        assert isinstance(result, StructuredAssistantContent)
        assert result.answer == ""
        assert result.sql is None
        assert result.suggestions == []
        assert result.thinking is None

    def test_whitespace_only_input_returns_empty_content(self):
        """Verify whitespace-only input is treated as empty."""
        result = parse_structured_response_json("   \n\t  ")
        assert isinstance(result, StructuredAssistantContent)
        assert result.answer == ""

    def test_empty_string_preserves_thinking_when_provided(self):
        """Verify thinking argument is forwarded on the empty-guard path."""
        result = parse_structured_response_json("", thinking="reasoning text")
        assert result.thinking == "reasoning text"

    def test_empty_string_normalises_thinking_whitespace(self):
        """Verify thinking is stripped even on the empty-guard path."""
        result = parse_structured_response_json("", thinking="  hi  ")
        assert result.thinking == "hi"

    def test_empty_string_without_thinking_yields_none(self):
        """Verify thinking stays None when not provided."""
        result = parse_structured_response_json("")
        assert result.thinking is None

    def test_valid_json_still_parses_after_guard(self):
        """Verify the guard does not regress valid JSON parsing."""
        result = parse_structured_response_json(
            '{"answer": "hi", "sql": null, "suggestions": ["next"]}'
        )
        assert result.answer == "hi"
        assert result.sql is None
        assert result.suggestions == ["next"]

    def test_json_answer_suggestion_block_is_not_rendered_twice(self):
        """Strip legacy suggestions blocks accidentally placed inside JSON answer."""
        result = parse_structured_response_json(
            "\n".join(
                [
                    "{",
                    (
                        '  "answer": "Main answer.\\n\\n```suggestions\\n'
                        'Open logs\\nCheck config\\n```",'
                    ),
                    '  "sql": null,',
                    '  "suggestions": ["Open logs", "Check config", "Restart service"]',
                    "}",
                ]
            )
        )
        assert result.answer == "Main answer."
        assert result.sql is None
        assert result.suggestions == [
            "Open logs",
            "Check config",
            "Restart service",
        ]

    def test_json_answer_sql_block_is_extracted_when_sql_field_is_empty(self):
        """Recover SQL blocks accidentally placed inside JSON answer."""
        result = parse_structured_response_json(
            "\n".join(
                [
                    "{",
                    '  "answer": "Main answer.\\n\\n```sql\\nSELECT 1\\n```",',
                    '  "sql": null,',
                    '  "suggestions": []',
                    "}",
                ]
            )
        )
        assert result.answer == "Main answer."
        assert result.sql == "SELECT 1"
        assert result.suggestions == []
