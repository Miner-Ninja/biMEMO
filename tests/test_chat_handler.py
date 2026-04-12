"""Tests for orchestrator chat-response shaping helpers."""

from backend.processes.orchestrator.chat_handler import _build_structured_content


def test_build_structured_content_extracts_sql_and_suggestions() -> None:
    """Extract SQL and suggestion blocks from one assistant answer."""
    structured = _build_structured_content(
        "\n".join(
            [
                "Main answer.",
                "",
                "```sql",
                "SELECT 1",
                "```",
                "",
                "```suggestions",
                "Next step",
                "Another step",
                "```",
            ]
        )
    )
    assert structured.answer == "Main answer."
    assert structured.sql == "SELECT 1"
    assert structured.suggestions == ["Next step", "Another step"]


def test_build_structured_content_drops_language_mismatched_suggestions() -> None:
    """Drop suggestions when their script does not match the answer."""
    structured = _build_structured_content(
        "\n".join(
            [
                "Русский ответ.",
                "",
                "```suggestions",
                "Open the report",
                "Check the dashboard",
                "```",
            ]
        )
    )
    assert structured.answer == "Русский ответ."
    assert structured.suggestions == []
