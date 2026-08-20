"""Tests for orchestrator tool setup."""

from backend.files.paths import uploaded_files_dir
from backend.processes.orchestrator.tool_setup import _build_fs_allowed_dirs


def test_fs_allowed_dirs_include_database_and_uploaded_files_dirs(tmp_path):
    """File tools should read DB-adjacent files and uploaded chat files."""
    data_dir = str(tmp_path / "db")

    assert _build_fs_allowed_dirs(data_dir) == [
        data_dir,
        str(uploaded_files_dir()),
    ]
