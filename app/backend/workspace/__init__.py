"""Workspace history module.

Provides tracking, persistence, and context injection for
workspace actions (file ops, commands, git operations).
"""

from .history_context import build_history_context, format_history_entry
from .history_store import WorkspaceHistoryStore
from .history_tracker import WorkspaceHistoryEntry, WorkspaceHistoryTracker


__all__ = [
    "WorkspaceHistoryEntry",
    "WorkspaceHistoryStore",
    "WorkspaceHistoryTracker",
    "build_history_context",
    "format_history_entry",
]
