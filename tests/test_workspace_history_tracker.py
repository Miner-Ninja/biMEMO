"""Tests for workspace history tracker.

Covers action collection, note merging, entry building,
and turn reset logic.
"""

from backend.workspace.history_tracker import (
    WorkspaceHistoryEntry,
    WorkspaceHistoryTracker,
)


def test_entry_to_dict_roundtrip():
    """to_dict and from_dict round-trip an entry without losing any field."""
    entry = WorkspaceHistoryEntry(
        turn=3,
        timestamp="2024-01-01T00:00:00",
        files_read=["a.py", "b.py"],
        files_written=[{"path": "c.py", "status": "success"}],
        commands_run=[{"cmd": "ls"}],
        errors=[{"source": "file_read", "issue": "no such file"}],
        git={"git_status": True},
        user_intent="fix bug",
        progress="halfway",
        decisions=["use path B"],
        blockers=["missing dep"],
    )
    data = entry.to_dict()
    restored = WorkspaceHistoryEntry.from_dict(data)
    assert restored.turn == 3
    assert restored.files_read == ["a.py", "b.py"]
    assert restored.user_intent == "fix bug"
    assert restored.decisions == ["use path B"]
    assert restored.blockers == ["missing dep"]


def test_entry_defaults():
    """A fresh entry defaults its list fields to empty and git to None."""
    entry = WorkspaceHistoryEntry(turn=0, timestamp="")
    assert entry.files_read == []
    assert entry.files_written == []
    assert entry.commands_run == []
    assert entry.errors == []
    assert entry.git is None
    assert entry.user_intent == ""
    assert entry.progress == ""
    assert entry.decisions == []
    assert entry.blockers == []


def test_tracker_reset_turn():
    """reset_turn advances the turn but keeps level-2 fields like user_intent."""
    tracker = WorkspaceHistoryTracker()
    tracker.reset_turn(1)
    tracker.add_note(user_intent="start")
    assert tracker.turn == 1

    tracker.reset_turn(2)
    assert tracker.turn == 2
    # Level 2 fields persist across resets
    assert tracker._user_intent == "start"


def test_tracker_add_note():
    """add_note stores semantic fields; a second call extends decisions."""
    tracker = WorkspaceHistoryTracker()
    tracker.reset_turn(1)
    tracker.add_note(
        user_intent="fix bug",
        progress="started",
        decisions=["use regex"],
        blockers=["none"],
    )
    assert tracker._user_intent == "fix bug"
    assert tracker._progress == "started"
    assert tracker._decisions == ["use regex"]
    assert tracker._blockers == ["none"]

    # Second call should extend decisions
    tracker.add_note(decisions=["also use lru_cache"])
    assert tracker._decisions == ["use regex", "also use lru_cache"]


def test_tracker_load_file_read_actions():
    """file_read paths are deduplicated; failed attempts keep their error entry."""
    tracker = WorkspaceHistoryTracker()
    tracker.reset_turn(1)
    tracker.load_actions_from_broker(
        [
            {"tool": "file_read", "params": {"path": "a.py"}, "success": True},
            {"tool": "file_read", "params": {"path": "b.py"}, "success": True},
            {
                "tool": "file_read",
                "params": {"path": "a.py"},
                "success": True,
            },  # duplicate
            {
                "tool": "file_read",
                "params": {"path": "c.py"},
                "success": False,
                "error": "not found",
            },
        ]
    )
    # c.py is added even on failure — the attempt is tracked
    assert tracker._files_read == ["a.py", "b.py", "c.py"]
    assert len(tracker._errors) == 1
    assert tracker._errors[0]["source"] == "file_read"


def test_tracker_load_file_write_actions():
    """file_write actions are recorded with a status; failures add an error."""
    tracker = WorkspaceHistoryTracker()
    tracker.reset_turn(1)
    tracker.load_actions_from_broker(
        [
            {"tool": "file_write", "params": {"path": "x.py"}, "success": True},
            {
                "tool": "file_write",
                "params": {"path": "y.py"},
                "success": False,
                "error": "perm",
            },
        ]
    )
    assert len(tracker._files_written) == 2
    assert tracker._files_written[0]["status"] == "success"
    assert tracker._files_written[1]["status"] == "error"
    assert len(tracker._errors) == 1


def test_tracker_load_command_actions():
    """execute_command and stream variants populate the commands-run list."""
    tracker = WorkspaceHistoryTracker()
    tracker.reset_turn(1)
    tracker.load_actions_from_broker(
        [
            {"tool": "execute_command", "params": {"command": "ls"}, "success": True},
            {
                "tool": "execute_command_stream",
                "params": {"cmd": "cat x"},
                "success": False,
                "error": "fail",
            },
        ]
    )
    assert len(tracker._commands_run) == 2
    assert tracker._commands_run[0]["cmd"] == "ls"
    assert tracker._commands_run[1]["cmd"] == "cat x"
    assert tracker._commands_run[1]["error"] == "fail"


def test_tracker_load_git_actions():
    """git_status and git_commit set the corresponding git info flags."""
    tracker = WorkspaceHistoryTracker()
    tracker.reset_turn(1)
    tracker.load_actions_from_broker(
        [
            {"tool": "git_status", "params": {}, "success": True},
            {"tool": "git_commit", "params": {}, "success": True},
        ]
    )
    assert tracker._git_info is not None
    assert tracker._git_info["git_status"] is True
    assert tracker._git_info["git_commit"] is True


def test_tracker_build_entry():
    """build_entry assembles an entry from the buffered turn, notes, and actions."""
    tracker = WorkspaceHistoryTracker()
    tracker.reset_turn(5)
    tracker.add_note(user_intent="refactor", progress="done")
    tracker.load_actions_from_broker(
        [
            {"tool": "file_read", "params": {"path": "main.py"}, "success": True},
            {"tool": "file_write", "params": {"path": "main.py"}, "success": True},
        ]
    )
    entry = tracker.build_entry()
    assert entry.turn == 5
    assert entry.user_intent == "refactor"
    assert entry.progress == "done"
    assert entry.files_read == ["main.py"]
    assert len(entry.files_written) == 1


def test_tracker_has_actions():
    """has_actions is False before any action is loaded, True after one."""
    tracker = WorkspaceHistoryTracker()
    tracker.reset_turn(1)
    assert not tracker.has_actions()
    tracker.load_actions_from_broker(
        [
            {"tool": "file_read", "params": {"path": "x.py"}, "success": True},
        ]
    )
    assert tracker.has_actions()


def test_tracker_clears_buffers_on_reset():
    """reset_turn clears the files, commands, errors, and git info buffers."""
    tracker = WorkspaceHistoryTracker()
    tracker.reset_turn(1)
    tracker.load_actions_from_broker(
        [
            {"tool": "file_read", "params": {"path": "a.py"}, "success": True},
        ]
    )
    assert len(tracker._files_read) == 1
    tracker.reset_turn(2)
    assert len(tracker._files_read) == 0
    assert len(tracker._files_written) == 0
    assert len(tracker._commands_run) == 0
    assert len(tracker._errors) == 0
    assert tracker._git_info is None
