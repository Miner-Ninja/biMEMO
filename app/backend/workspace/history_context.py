"""Workspace history context injection.

Formats history entries into a compact text block for injection
into the agent's context (especially after compression).
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from .history_tracker import WorkspaceHistoryEntry


def format_history_entry(entry: WorkspaceHistoryEntry) -> str:
    """Format one history entry as a compact text block."""
    lines: list[str] = []

    if entry.files_read:
        lines.append(f"Files read ({len(entry.files_read)}):")
        for f in entry.files_read[:10]:
            lines.append(f"  - {f}")
        if len(entry.files_read) > 10:
            lines.append(f"  ... and {len(entry.files_read) - 10} more")

    if entry.files_written:
        lines.append(f"Files written ({len(entry.files_written)}):")
        for fw in entry.files_written[:10]:
            path = fw.get("path", "?")
            status = fw.get("status", "unknown")
            lines.append(f"  - {path} [{status}]")

    if entry.commands_run:
        lines.append(f"Commands run ({len(entry.commands_run)}):")
        for cmd in entry.commands_run[:10]:
            cmd_str = cmd.get("cmd", "")
            error = cmd.get("error")
            suffix = f" (error: {error[:50]})" if error else ""
            lines.append(f"  - {cmd_str}{suffix}")

    if entry.errors:
        lines.append(f"Errors ({len(entry.errors)}):")
        for err in entry.errors[:5]:
            source = err.get("source", "?")
            issue = err.get("issue", "")[:80]
            lines.append(f"  - {source}: {issue}")

    if entry.user_intent:
        lines.append(f"Intent: {entry.user_intent}")

    if entry.progress:
        lines.append(f"Progress: {entry.progress}")

    if entry.decisions:
        lines.append("Decisions:")
        for d in entry.decisions[:5]:
            lines.append(f"  - {d}")

    if entry.blockers:
        lines.append("Blockers:")
        for b in entry.blockers[:5]:
            lines.append(f"  - {b}")

    return "\n".join(lines)


def build_history_context(
    entries: list[WorkspaceHistoryEntry],
    *,
    max_entries: int = 5,
) -> str:
    """Build a compact context block from recent history entries.

    Returns empty string if there are no entries.
    """
    if not entries:
        return ""

    recent = entries[-max_entries:]
    blocks: list[str] = []

    for entry in recent:
        block = format_history_entry(entry)
        if block:
            blocks.append(f"Turn {entry.turn}:\n{block}")

    if not blocks:
        return ""

    return (
        "Workspace history (recent turns):\n"
        "Actions from earlier turns that were compressed.\n\n" + "\n---\n".join(blocks)
    )
