"""Shared filesystem paths for uploaded chat files."""

from pathlib import Path

from backend.config import get_settings


def uploaded_files_dir() -> Path:
    """Return the configured directory where uploaded chat files are stored."""
    return Path(get_settings().uploaded_files_dir)


__all__ = ["uploaded_files_dir"]
