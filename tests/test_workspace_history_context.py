"""Tests for workspace history context formatting.

Covers entry formatting, context building, and edge cases.
"""

from backend.workspace.history_context import (
    build_history_context,
    format_history_entry,
)
from backend.workspace.history_tracker import WorkspaceHistoryEntry


def test_format_empty_entry():
    entry = WorkspaceHistoryEntry(turn=1, timestamp="")
    result = format_history_entry(entry)
    assert result == ""


def test_format_files_read():
    entry = WorkspaceHistoryEntry(
        turn=1,
        timestamp="",
        files_read=["a.py", "b.py", "c.py"],
    )
    result = format_history_entry(entry)
    assert "Files read (3):" in result
    assert "a.py" in result
    assert "b.py" in result


def test_format_files_read_truncated():
    entry = WorkspaceHistoryEntry(
        turn=1,
        timestamp="",
        files_read=[f"file{i}.py" for i in range(15)],
    )
    result = format_history_entry(entry)
    assert "Files read (15):" in result
    assert "... and 5 more" in result


def test_format_files_written():
    entry = WorkspaceHistoryEntry(
        turn=1,
        timestamp="",
        files_written=[
            {"path": "a.py", "status": "success"},
            {"path": "b.py", "status": "error"},
        ],
    )
    result = format_history_entry(entry)
    assert "Files written (2):" in result
    assert "a.py [success]" in result
    assert "b.py [error]" in result


def test_format_commands():
    entry = WorkspaceHistoryEntry(
        turn=1,
        timestamp="",
        commands_run=[
            {"cmd": "ls -la"},
            {"cmd": "cat x.py", "error": "not found"},
        ],
    )
    result = format_history_entry(entry)
    assert "Commands run (2):" in result
    assert "ls -la" in result
    assert "error: not found" in result


def test_format_errors():
    entry = WorkspaceHistoryEntry(
        turn=1,
        timestamp="",
        errors=[
            {"source": "file_read", "issue": "no such file"},
            {"source": "execute_command", "issue": "permission denied"},
        ],
    )
    result = format_history_entry(entry)
    assert "Errors (2):" in result
    assert "file_read: no such file" in result


def test_format_semantic_fields():
    entry = WorkspaceHistoryEntry(
        turn=1,
        timestamp="",
        user_intent="fix login bug",
        progress="implemented fix, testing",
        decisions=["use bcrypt", "add rate limit"],
        blockers=["need DB access"],
    )
    result = format_history_entry(entry)
    assert "Intent: fix login bug" in result
    assert "Progress: implemented fix, testing" in result
    assert "use bcrypt" in result
    assert "need DB access" in result


def test_build_context_empty():
    assert build_history_context([]) == ""


def test_build_context_single_entry():
    entries = [
        WorkspaceHistoryEntry(
            turn=5,
            timestamp="2024-01-01T00:00:00",
            files_read=["main.py"],
            user_intent="refactor",
        ),
    ]
    result = build_history_context(entries)
    assert "Workspace history" in result
    assert "Turn 5:" in result
    assert "main.py" in result


def test_build_context_multiple_entries():
    entries = [
        WorkspaceHistoryEntry(turn=1, timestamp="", files_read=["a.py"]),
        WorkspaceHistoryEntry(turn=2, timestamp="", files_read=["b.py"]),
        WorkspaceHistoryEntry(turn=3, timestamp="", files_read=["c.py"]),
    ]
    result = build_history_context(entries, max_entries=2)
    assert "Turn 2:" in result
    assert "Turn 3:" in result
    assert "Turn 1:" not in result  # truncated


def test_build_context_separator():
    entries = [
        WorkspaceHistoryEntry(turn=1, timestamp="", files_read=["a.py"]),
        WorkspaceHistoryEntry(turn=2, timestamp="", files_read=["b.py"]),
    ]
    result = build_history_context(entries)
    assert "---" in result


def test_build_context_empty_entries_filtered():
    # Entries with no actions should be filtered out
    entries = [
        WorkspaceHistoryEntry(turn=1, timestamp=""),
        WorkspaceHistoryEntry(turn=2, timestamp="", files_read=["x.py"]),
    ]
    result = build_history_context(entries)
    assert "Turn 1:" not in result
    assert "Turn 2:" in result
