"""Shared filesystem paths for uploaded chat files."""

from pathlib import Path


UPLOADED_FILES_DIR = Path("/data/files")


__all__ = ["UPLOADED_FILES_DIR"]
