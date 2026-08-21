"""Behaviour contract tests for the workspace routes (F27).

Covers the file-tree API against a real temporary workspace: listing the
root and subdirectories, parent-path computation, and the precise HTTP
errors for a missing path, a path pointing at a file, and an unreadable
directory.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.processes.fastapi_app.routes import workspace


class _FakeAuthState:
    """Minimal request.state carrying authenticated claims."""

    def __init__(self) -> None:
        self._state = {"claims": {"sub": "admin"}}


class _FakeRequest:
    """Minimal Request substitute for the auth guard."""

    def __init__(self) -> None:
        self.state = _FakeAuthState()


@pytest.fixture
def workspace_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Build a two-directory workspace and point the settings at it."""
    data_dir = tmp_path / "data"
    uploads_dir = tmp_path / "uploads"
    data_dir.mkdir()
    uploads_dir.mkdir()
    (data_dir / "db.sqlite").write_text("db")
    (data_dir / "note.txt").write_text("hello world")
    (data_dir / "sub").mkdir()
    (data_dir / "sub" / "inner.txt").write_text("inner")

    monkeypatch.setattr(
        "backend.config.get_settings",
        lambda: SimpleNamespace(database_path=data_dir / "db.sqlite"),
    )
    monkeypatch.setattr(
        "backend.files.paths.uploaded_files_dir",
        lambda: uploads_dir,
    )
    return data_dir, uploads_dir


class TestGetWorkspaceDirs:
    """GET /workspace/dirs: the configured directory list."""

    async def test_returns_both_directories(self, workspace_dirs: tuple[Path, Path]):
        """The data directory and the uploads directory are both listed."""
        data_dir, uploads_dir = workspace_dirs
        result = await workspace.get_workspace_dirs_endpoint(_FakeRequest())
        assert result == {"directories": [str(data_dir), str(uploads_dir)]}


class TestGetWorkspaceTree:
    """GET /workspace/tree: listing, parent path, and error mapping."""

    async def test_root_listing(self, workspace_dirs: tuple[Path, Path]):
        """The root lists entries of the first workspace directory."""
        data_dir, _ = workspace_dirs
        result = await workspace.get_workspace_tree(_FakeRequest(), path="")
        assert result["path"] == ""
        assert result["parent"] is None
        assert result["workspace"] == str(data_dir)
        names = {item["name"]: item for item in result["items"]}
        assert names["note.txt"]["type"] == "file"
        assert names["note.txt"]["size"] == len("hello world")
        assert names["sub"]["type"] == "directory"
        assert names["sub"]["path"] == "sub"

    async def test_subdirectory_listing(self, workspace_dirs: tuple[Path, Path]):
        """A subdirectory is listed relative to its workspace base."""
        _, _ = workspace_dirs
        result = await workspace.get_workspace_tree(_FakeRequest(), path="sub")
        names = {item["name"]: item for item in result["items"]}
        assert names["inner.txt"]["type"] == "file"
        assert names["inner.txt"]["path"] == "sub/inner.txt"
        assert result["parent"] == "."

    async def test_parent_of_nested_path(self, workspace_dirs: tuple[Path, Path]):
        """The parent of a nested path is its first component."""
        data_dir, _ = workspace_dirs
        (data_dir / "a" / "b").mkdir(parents=True)
        result = await workspace.get_workspace_tree(_FakeRequest(), path="a/b")
        assert result["parent"] == "a"

    async def test_missing_path_is_404(self, workspace_dirs: tuple[Path, Path]):
        """A path that exists in no workspace directory is a 404."""
        with pytest.raises(HTTPException) as excinfo:
            await workspace.get_workspace_tree(_FakeRequest(), path="nope")
        assert excinfo.value.status_code == 404
        assert "nope" in excinfo.value.detail

    async def test_file_path_is_400(self, workspace_dirs: tuple[Path, Path]):
        """Listing a file (not a directory) is a 400."""
        with pytest.raises(HTTPException) as excinfo:
            await workspace.get_workspace_tree(_FakeRequest(), path="note.txt")
        assert excinfo.value.status_code == 400
        assert "note.txt" in excinfo.value.detail

    async def test_unreadable_directory_is_403(
        self, workspace_dirs: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ):
        """A directory that raises PermissionError on iteration is a 403."""

        def _raise_permission_error(self: Path):
            raise PermissionError(13, "Permission denied", str(self))

        monkeypatch.setattr(Path, "iterdir", _raise_permission_error)
        with pytest.raises(HTTPException) as excinfo:
            await workspace.get_workspace_tree(_FakeRequest(), path="")
        assert excinfo.value.status_code == 403
