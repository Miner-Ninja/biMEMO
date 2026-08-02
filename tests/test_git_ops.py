"""Tests for git operations and code search.

Covers git_status, git_diff, git_log, git_commit, git_push,
and code_search functionality.
"""

import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from backend.tools.handlers.code_search import code_search
from backend.tools.handlers.git_ops import (
    git_commit,
    git_diff,
    git_log,
    git_push,
    git_status,
)


def _run_git_sync(repo: str, *args: str) -> subprocess.CompletedProcess:
    """Run a git command synchronously."""
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def _setup_git_repo() -> str:
    """Create a temporary git repo for testing."""
    tmpdir = tempfile.mkdtemp()
    _run_git_sync(tmpdir, "init", "-b", "main")
    _run_git_sync(tmpdir, "config", "user.email", "test@test.com")
    _run_git_sync(tmpdir, "config", "user.name", "Test")
    return tmpdir


def _add_file(repo: str, name: str, content: str) -> None:
    """Add a file to the repo."""
    path = Path(repo) / name
    path.write_text(content)
    _run_git_sync(repo, "add", name)


def _commit(repo: str, message: str) -> None:
    """Commit changes in the repo."""
    _run_git_sync(repo, "commit", "-m", message)


# ---------------------------------------------------------------------------
# git_status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_git_status_clean():
    repo = _setup_git_repo()
    try:
        _add_file(repo, "README.md", "# Hello")
        _commit(repo, "initial")

        result = await git_status(cwd=repo)
        assert "error" not in result
        assert result["clean"] is True
        assert result["branch"] == "main"
    finally:
        shutil.rmtree(repo)


@pytest.mark.asyncio
async def test_git_status_with_changes():
    repo = _setup_git_repo()
    try:
        _add_file(repo, "README.md", "# Hello")
        _commit(repo, "initial")

        # Modify a file
        (Path(repo) / "README.md").write_text("# Hello World")

        result = await git_status(cwd=repo)
        assert "error" not in result
        assert result["clean"] is False
    finally:
        shutil.rmtree(repo)


# ---------------------------------------------------------------------------
# git_diff
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_git_diff_no_changes():
    repo = _setup_git_repo()
    try:
        _add_file(repo, "README.md", "# Hello")
        _commit(repo, "initial")

        result = await git_diff(cwd=repo)
        assert "error" not in result
        assert result["has_changes"] is False
    finally:
        shutil.rmtree(repo)


@pytest.mark.asyncio
async def test_git_diff_with_changes():
    repo = _setup_git_repo()
    try:
        _add_file(repo, "test.py", "print('hello')")
        _commit(repo, "initial")

        # Modify
        (Path(repo) / "test.py").write_text("print('world')")

        result = await git_diff(cwd=repo)
        assert "error" not in result
        assert result["has_changes"] is True
        assert "diff" in result
    finally:
        shutil.rmtree(repo)


# ---------------------------------------------------------------------------
# git_log
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_git_log():
    repo = _setup_git_repo()
    try:
        _add_file(repo, "a.txt", "a")
        _commit(repo, "commit one")
        _add_file(repo, "b.txt", "b")
        _commit(repo, "commit two")

        result = await git_log(cwd=repo, count=5)
        assert "error" not in result
        assert result["count"] == 2
        assert len(result["commits"]) == 2
        assert result["commits"][0]["message"] == "commit two"
        assert result["commits"][1]["message"] == "commit one"
    finally:
        shutil.rmtree(repo)


# ---------------------------------------------------------------------------
# git_commit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_git_commit():
    repo = _setup_git_repo()
    try:
        _add_file(repo, "test.txt", "content")

        result = await git_commit(message="add test", cwd=repo)
        assert "error" not in result
        assert result["status"] == "committed"
        assert result["hash"]  # Should have a commit hash
    finally:
        shutil.rmtree(repo)


@pytest.mark.asyncio
async def test_git_commit_nothing_to_commit():
    repo = _setup_git_repo()
    try:
        _add_file(repo, "README.md", "# Hello")
        _commit(repo, "initial")

        result = await git_commit(message="empty commit", cwd=repo)
        # Should handle "nothing to commit" gracefully
        assert result.get("status") == "nothing to commit" or "error" not in result
    finally:
        shutil.rmtree(repo)


# ---------------------------------------------------------------------------
# git_push (will fail without remote, but should not crash)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_git_push_no_remote():
    repo = _setup_git_repo()
    try:
        result = await git_push(cwd=repo)
        # Should fail gracefully without a remote
        assert "error" in result or result.get("status") != "pushed"
    finally:
        shutil.rmtree(repo)


# ---------------------------------------------------------------------------
# code_search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_code_search_text():
    repo = _setup_git_repo()
    try:
        _add_file(repo, "hello.py", "def hello():\n    print('world')")
        _add_file(repo, "other.py", "def other():\n    pass")
        _commit(repo, "initial")

        result = await code_search(pattern="hello", cwd=repo)
        assert "error" not in result
        assert result["count"] > 0
        assert any("hello.py" in m["file"] for m in result["matches"])
    finally:
        shutil.rmtree(repo)


@pytest.mark.asyncio
async def test_code_search_no_match():
    repo = _setup_git_repo()
    try:
        _add_file(repo, "test.txt", "some content")
        _commit(repo, "initial")

        result = await code_search(pattern="nonexistent_xyz", cwd=repo)
        assert "error" not in result
        assert result["count"] == 0
    finally:
        shutil.rmtree(repo)


@pytest.mark.asyncio
async def test_code_search_empty_pattern():
    result = await code_search(pattern="")
    assert "error" in result


@pytest.mark.asyncio
async def test_code_search_regex():
    repo = _setup_git_repo()
    try:
        _add_file(repo, "nums.txt", "123\n456\n789")
        _commit(repo, "initial")

        # Use POSIX ERE (grep -E), not PCRE — \d is PCRE
        result = await code_search(
            pattern=r"^[0-9]{3}$",
            cwd=repo,
            use_regex=True,
        )
        assert "error" not in result
        # Should find all 3 lines
        assert result["count"] >= 3
    finally:
        shutil.rmtree(repo)
