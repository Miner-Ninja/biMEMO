"""Git operations for workspace agent.

Provides read-only git tools (git_status, git_diff, git_log) and
write tools (git_commit, git_push) executed via cmd_worker.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from backend.workers.cmd_worker import execute_command


logger = logging.getLogger(__name__)


@dataclass
class GitResult:
    """Result of a git operation."""

    output: str
    exit_code: int
    error: str | None = None


async def _run_git(
    *args: str,
    cwd: str | None = None,
    timeout_seconds: float = 30.0,
) -> GitResult:
    """Run a git command and return structured result."""
    import shlex

    command = "git " + " ".join(shlex.quote(a) for a in args)
    result = await execute_command(
        command=command,
        cwd=cwd,
        timeout=timeout_seconds,
    )
    return GitResult(
        output=result.stdout,
        exit_code=result.exit_code,
        error=result.error or (result.stderr if result.stderr else None),
    )


# ---------------------------------------------------------------------------
# Read-only tools
# ---------------------------------------------------------------------------


async def git_status(cwd: str | None = None) -> dict[str, Any]:
    """Show working tree status.

    Returns:
        Dict with status output, branch name, and summary counts.
    """
    result = await _run_git("status", "--porcelain", "-b", cwd=cwd)
    if result.exit_code != 0:
        return {"error": result.error or "git status failed"}

    lines = result.output.strip().split("\n") if result.output.strip() else []
    branch = "unknown"
    status_lines = []

    for line in lines:
        if line.startswith("## "):
            # Branch info line like "## main upstream.main"
            branch = line[3:].split()[0] if line[3:].split() else "unknown"
        else:
            status_lines.append(line)

    # Count changes
    staged = 0
    unstaged = 0
    untracked = 0
    for line in status_lines:
        if line.startswith("A ") or line.startswith("M ") or line.startswith("D "):
            staged += 1
        elif line.startswith(" M") or line.startswith("??"):
            if line.startswith("??"):
                untracked += 1
            else:
                unstaged += 1

    return {
        "branch": branch,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "clean": staged == 0 and unstaged == 0 and untracked == 0,
        "output": result.output,
    }


async def git_diff(
    cwd: str | None = None,
    staged: bool = False,
) -> dict[str, Any]:
    """Show differences in the working tree.

    Args:
        cwd: Working directory.
        staged: If True, show staged changes (--cached).

    Returns:
        Dict with diff output and summary.
    """
    args = ["diff", "--stat"]
    if staged:
        args.append("--cached")

    stat_result = await _run_git(*args, cwd=cwd)
    if stat_result.exit_code != 0:
        return {"error": stat_result.error or "git diff failed"}

    # Also get full diff
    full_args = ["diff", "-U5"]
    if staged:
        full_args.append("--cached")
    full_result = await _run_git(*full_args, cwd=cwd)

    return {
        "stat": stat_result.output,
        "diff": full_result.output,
        "has_changes": bool(full_result.output.strip()),
    }


async def git_log(
    cwd: str | None = None,
    count: int = 10,
) -> dict[str, Any]:
    """Show commit history.

    Args:
        cwd: Working directory.
        count: Number of commits to show (default 10).

    Returns:
        Dict with list of commits.
    """
    result = await _run_git(
        "log",
        "--oneline",
        f"-{count}",
        "--format=%H|%an|%ad|%s",
        "--date=short",
        cwd=cwd,
    )
    if result.exit_code != 0:
        return {"error": result.error or "git log failed"}

    commits = []
    for line in result.output.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) == 4:
            commits.append(
                {
                    "hash": parts[0][:8],
                    "author": parts[1],
                    "date": parts[2],
                    "message": parts[3],
                }
            )

    return {
        "commits": commits,
        "count": len(commits),
    }


# ---------------------------------------------------------------------------
# Write tools (RED zone)
# ---------------------------------------------------------------------------


async def git_commit(
    message: str,
    cwd: str | None = None,
    all_files: bool = False,
) -> dict[str, Any]:
    """Commit changes.

    Args:
        message: Commit message.
        cwd: Working directory.
        all_files: If True, auto-stage all tracked files (-a).

    Returns:
        Dict with commit hash and status.
    """
    # Stage if needed
    if all_files:
        stage_result = await _run_git("add", "-A", cwd=cwd)
        if stage_result.exit_code != 0:
            return {"error": f"Failed to stage: {stage_result.error}"}

    # Commit
    result = await _run_git("commit", "-m", message, cwd=cwd)
    if result.exit_code != 0:
        # Exit code 1 is normal when there's nothing to commit
        combined = (result.error or "") + (result.output or "")
        if "nothing to commit" in combined.lower():
            return {"status": "nothing to commit", "exit_code": 0}
        return {"error": result.error or "git commit failed"}

    # Extract commit hash
    commit_hash = ""
    for line in result.output.strip().split("\n"):
        line = line.strip()
        if line and not line.startswith("["):
            commit_hash = line[:8]
            break

    return {
        "status": "committed",
        "hash": commit_hash,
        "output": result.output,
    }


async def git_push(
    cwd: str | None = None,
    remote: str = "origin",
    branch: str | None = None,
) -> dict[str, Any]:
    """Push to remote.

    Args:
        cwd: Working directory.
        remote: Remote name (default 'origin').
        branch: Branch name (default = current branch).

    Returns:
        Dict with push result.
    """
    args = ["push", remote]
    if branch:
        args.append(branch)

    result = await _run_git(*args, cwd=cwd, timeout_seconds=60.0)
    if result.exit_code != 0:
        return {"error": result.error or "git push failed"}

    return {
        "status": "pushed",
        "remote": remote,
        "branch": branch or "current",
        "output": result.output,
    }
