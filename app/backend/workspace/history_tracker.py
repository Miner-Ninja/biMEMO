"""Workspace history tracker — collects workspace actions from ToolBroker.

After each agent turn, the orchestrator calls finalize_turn() to build
a history entry from collected actions, persists it, and clears the buffer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class WorkspaceHistoryEntry:
    """One persisted workspace history entry (per turn)."""

    turn: int
    timestamp: str
    files_read: list[str] = field(default_factory=list)
    files_written: list[dict] = field(default_factory=list)
    commands_run: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    git: dict | None = None
    # Level 2 — optional agent-reported fields
    user_intent: str = ""
    progress: str = ""
    decisions: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to dict for DB storage."""
        return {
            "turn": self.turn,
            "timestamp": self.timestamp,
            "files_read": self.files_read,
            "files_written": self.files_written,
            "commands_run": self.commands_run,
            "errors": self.errors,
            "git": self.git,
            "user_intent": self.user_intent,
            "progress": self.progress,
            "decisions": self.decisions,
            "blockers": self.blockers,
        }

    @classmethod
    def from_dict(cls, data: dict) -> WorkspaceHistoryEntry:
        """Deserialize from dict (DB row)."""
        return cls(
            turn=int(data.get("turn", 0)),
            timestamp=data.get("timestamp", ""),
            files_read=data.get("files_read", []),
            files_written=data.get("files_written", []),
            commands_run=data.get("commands_run", []),
            errors=data.get("errors", []),
            git=data.get("git"),
            user_intent=data.get("user_intent", ""),
            progress=data.get("progress", ""),
            decisions=data.get("decisions", []),
            blockers=data.get("blockers", []),
        )


class WorkspaceHistoryTracker:
    """Collects workspace actions and builds history entries.

    The orchestrator calls:
    - reset_turn() at the start of each turn
    - add_note() when the agent calls workspace_note()
    - finalize_turn() at the end of each turn to build and persist entry
    """

    def __init__(self) -> None:
        self._turn: int = 0
        self._files_read: list[str] = []
        self._files_written: list[dict] = []
        self._commands_run: list[dict] = []
        self._errors: list[dict] = []
        self._git_info: dict | None = None
        # Level 2 fields
        self._user_intent: str = ""
        self._progress: str = ""
        self._decisions: list[str] = []
        self._blockers: list[str] = []

    @property
    def turn(self) -> int:
        return self._turn

    def reset_turn(self, turn: int) -> None:
        """Prepare for a new turn."""
        self._turn = turn
        self._files_read.clear()
        self._files_written.clear()
        self._commands_run.clear()
        self._errors.clear()
        self._git_info = None
        # Keep Level 2 fields across turns (they accumulate)

    def add_note(
        self,
        *,
        user_intent: str = "",
        progress: str = "",
        decisions: list[str] | None = None,
        blockers: list[str] | None = None,
    ) -> None:
        """Merge agent-reported notes into current state."""
        if user_intent:
            self._user_intent = user_intent
        if progress:
            self._progress = progress
        if decisions:
            self._decisions.extend(decisions)
        if blockers:
            self._blockers.extend(blockers)

    def load_actions_from_broker(self, actions: list[dict]) -> None:
        """Process raw actions collected by ToolBroker."""
        for action in actions:
            tool = action.get("tool", "")
            params = action.get("params", {})
            success = action.get("success", True)

            if tool == "file_read":
                path = params.get("path", "")
                if path and path not in self._files_read:
                    self._files_read.append(path)
                if not success:
                    self._errors.append(
                        {
                            "source": "file_read",
                            "issue": action.get("error", "read failed"),
                        }
                    )

            elif tool == "file_write":
                path = params.get("path", "")
                entry: dict = {"path": path}
                if success:
                    entry["status"] = "success"
                    # Size is not known here — will be in result
                else:
                    entry["status"] = "error"
                    self._errors.append(
                        {
                            "source": "file_write",
                            "issue": action.get("error", "write failed"),
                        }
                    )
                self._files_written.append(entry)

            elif tool in ("execute_command", "execute_command_stream"):
                cmd = params.get("command", params.get("cmd", ""))
                cmd_entry: dict = {"cmd": cmd}
                if not success:
                    cmd_entry["error"] = action.get("error", "command failed")
                    self._errors.append(
                        {
                            "source": tool,
                            "issue": cmd_entry["error"],
                        }
                    )
                self._commands_run.append(cmd_entry)

            elif tool in (
                "git_status",
                "git_diff",
                "git_log",
                "git_commit",
                "git_push",
            ):
                if self._git_info is None:
                    self._git_info = {}
                self._git_info[tool] = True
                if not success:
                    self._errors.append(
                        {
                            "source": tool,
                            "issue": action.get("error", "git operation failed"),
                        }
                    )

    def build_entry(self) -> WorkspaceHistoryEntry:
        """Build a history entry from collected data."""
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        return WorkspaceHistoryEntry(
            turn=self._turn,
            timestamp=now,
            files_read=list(self._files_read),
            files_written=list(self._files_written),
            commands_run=list(self._commands_run),
            errors=list(self._errors),
            git=dict(self._git_info) if self._git_info else None,
            user_intent=self._user_intent,
            progress=self._progress,
            decisions=list(self._decisions),
            blockers=list(self._blockers),
        )

    def has_actions(self) -> bool:
        """Check if there are any actions to persist."""
        return (
            bool(self._files_read)
            or bool(self._files_written)
            or bool(self._commands_run)
            or bool(self._errors)
            or bool(self._git_info)
        )
