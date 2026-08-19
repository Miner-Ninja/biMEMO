"""Tests for the structured-response parser defensive guard.

Covers parse_structured_response_json behaviour on empty and
whitespace-only inputs, which previously raised JSONDecodeError and
crashed the client, and parse_final_only_response_json handling of a
stray suggestions block inside the answer.
"""

import pytest

from backend.llm.structured_response import (
    StructuredAssistantContent,
    parse_final_only_response_json,
    parse_structured_response_json,
)


class TestParseStructuredResponseJsonEmptyGuard:
    """Guard against empty/whitespace input that previously crashed the parser."""

    def test_empty_string_returns_empty_content(self):
        """Verify parse returns empty content, not raises, on empty string."""
        result = parse_structured_response_json("")
        assert isinstance(result, StructuredAssistantContent)
        assert result.answer == ""
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
        assert result.suggestions == [
            "Open logs",
            "Check config",
            "Restart service",
        ]


class TestParseFinalOnlyResponseJsonSuggestionsBlock:
    """A stray suggestions block in the answer is stripped, never shown twice."""

    def test_block_with_empty_field_moves_into_suggestions(self):
        """A block in the answer becomes suggestions; the answer stays clean."""
        result = parse_final_only_response_json(
            "\n".join(
                [
                    "{",
                    (
                        '  "answer": "На связи. Что дальше?\\n\\n```suggestions\\n'  # noqa: RUF001
                        'Покажи статус\\nПокажи каталоги\\nНайди ошибки\\n```",'  # noqa: RUF001
                    ),
                    '  "suggestions": []',
                    "}",
                ]
            )
        )
        assert result.answer == "На связи. Что дальше?"  # noqa: RUF001
        assert result.suggestions == [
            "Покажи статус",
            "Покажи каталоги",
            "Найди ошибки",
        ]

    def test_block_merges_with_field_items_without_duplicates(self):
        """Block lines already present in the field are not repeated."""
        result = parse_final_only_response_json(
            "\n".join(
                [
                    "{",
                    (
                        '  "answer": "Main answer.\\n\\n```suggestions\\n'
                        'Open logs\\nCheck config\\n```",'
                    ),
                    '  "suggestions": ["Open logs", "Restart service"]',
                    "}",
                ]
            )
        )
        assert result.answer == "Main answer."
        assert result.suggestions == ["Open logs", "Restart service", "Check config"]

    def test_language_mismatched_block_yields_no_suggestions(self):
        """A block in a different script than the answer is dropped."""
        result = parse_final_only_response_json(
            "\n".join(
                [
                    "{",
                    '  "answer": "Привет! Я на связи.\\n\\n```suggestions\\n'
                    'Show git status\\nShow workspace dirs\\n```",'
                    '  "suggestions": []',
                    "}",
                ]
            )
        )
        assert result.answer == "Привет! Я на связи."
        assert result.suggestions == []

    def test_block_only_answer_is_rejected(self):
        """An answer consisting only of a suggestions block has no visible text."""
        with pytest.raises(ValueError, match="final answer is empty"):
            parse_final_only_response_json(
                "\n".join(
                    [
                        "{",
                        '  "answer": "```suggestions\\nOpen logs\\n```",',
                        '  "suggestions": []',
                        "}",
                    ]
                )
            )

    def test_plain_answer_passes_through_unchanged(self):
        """Without a block, answer and field suggestions are kept as-is."""
        result = parse_final_only_response_json(
            '{"answer": "Done.", "suggestions": ["Next step"]}'
        )
        assert result.answer == "Done."
        assert result.suggestions == ["Next step"]
