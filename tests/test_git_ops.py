"""Tests for git operations and code search.

Covers git_status, git_diff, git_log, git_commit, git_push,
and code_search functionality.
"""

import asyncio
import importlib
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.tools.handlers.code_search import code_search
from backend.tools.handlers.git_ops import (
    git_commit,
    git_diff,
    git_log,
    git_push,
    git_status,
)


code_search_module = importlib.import_module("backend.tools.handlers.code_search")


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


@pytest.mark.asyncio
async def test_code_search_excludes_sensitive_files():
    """Recursive code search never reads environment or private-key content."""
    repo = _setup_git_repo()
    try:
        _add_file(repo, ".env", "SHARED_SECRET=needle")
        (Path(repo) / ".ssh").mkdir()
        _add_file(repo, ".ssh/id_rsa", "needle private")
        _add_file(repo, "public.txt", "needle public")

        result = await code_search(pattern="needle", cwd=repo)

        assert result["count"] == 1
        assert result["matches"][0]["file"].endswith("public.txt")
    finally:
        shutil.rmtree(repo)


@pytest.mark.asyncio
async def test_code_search_rejects_root_symlink_swap(tmp_path):
    """A canonical search root replaced by a symlink cannot escape."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("needle")
    root.rmdir()
    root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(OSError, match="symbolic link|Too many levels|Not a directory"):
        await code_search(pattern="needle", cwd=str(root))


@pytest.mark.asyncio
async def test_code_search_bounds_pathological_regex(tmp_path):
    """A backtracking expression cannot occupy a worker indefinitely."""
    (tmp_path / "input.txt").write_text("a" * 50_000 + "!")

    result = await asyncio.wait_for(
        code_search(pattern="(a+)+$", cwd=str(tmp_path), use_regex=True),
        timeout=2,
    )

    assert result["count"] == 0


@pytest.mark.asyncio
async def test_code_search_skips_fifo_without_blocking(tmp_path):
    """A named pipe is never opened as a blocking searchable file."""
    os.mkfifo(tmp_path / "blocking.pipe")
    (tmp_path / "public.txt").write_text("needle")

    result = await asyncio.wait_for(
        code_search(pattern="needle", cwd=str(tmp_path)),
        timeout=2,
    )

    assert result["count"] == 1
    assert result["matches"][0]["file"] == "public.txt"


@pytest.mark.asyncio
async def test_code_search_counts_bytes_read_after_file_growth(tmp_path, monkeypatch):
    """The aggregate bound uses actual reads rather than stale metadata size."""
    (tmp_path / "growing.txt").write_text("12345x")
    monkeypatch.setattr(code_search_module, "_MAX_FILE_BYTES", 10)
    monkeypatch.setattr(code_search_module, "_MAX_AGGREGATE_BYTES", 5)
    monkeypatch.setattr(
        code_search_module.os,
        "fstat",
        lambda _fd: SimpleNamespace(st_mode=stat.S_IFREG, st_size=0),
    )

    result = await code_search(pattern="x", cwd=str(tmp_path))

    assert result["count"] == 0
