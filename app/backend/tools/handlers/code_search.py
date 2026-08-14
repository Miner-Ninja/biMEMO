"""Code search — grep-based text search across workspace files.

Searches for text or regex patterns in files within the workspace
directory. Returns matching files with line numbers and context.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import regex

from backend.tools.validation import ValidationError, check_sensitive_read_path
from backend.workers.fs_worker import open_directory_no_symlinks


logger = logging.getLogger(__name__)

_MAX_PATTERN_LENGTH = 4096
_MAX_FILES_SCANNED = 5000
_MAX_AGGREGATE_BYTES = 32 * 1024 * 1024
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_LINE_LENGTH = 64 * 1024
_SEARCH_TIMEOUT_SECONDS = 30.0
_REGEX_TIMEOUT_SECONDS = 0.01


@dataclass
class SearchMatch:
    """One match from code search."""

    file: str
    line: int
    text: str


async def code_search(
    pattern: str,
    cwd: str | None = None,
    file_pattern: str = "**/*",
    max_results: int = 50,
    context_lines: int = 2,
    use_regex: bool = False,
) -> dict[str, Any]:
    """Search for text/regex patterns across workspace files.

    Traverses files directly so safety checks run before every open.

    Args:
        pattern: Text or regex pattern to search for.
        cwd: Working directory (workspace root).
        file_pattern: Glob pattern for files to search (default: all).
        max_results: Maximum number of matches to return.
        context_lines: Number of context lines before/after match.
        use_regex: If True, treat pattern as regex.

    Returns:
        Dict with matches, file count, and total matches.
    """
    if not pattern:
        return {"error": "pattern is required", "matches": [], "count": 0}
    if len(pattern) > _MAX_PATTERN_LENGTH:
        return {"error": "pattern is too long", "matches": [], "count": 0}

    try:
        matcher = regex.compile(pattern) if use_regex else None
    except regex.error as exc:
        return {"error": f"invalid regex: {exc}", "matches": [], "count": 0}

    def search() -> tuple[list[SearchMatch], set[str]]:
        root = Path(os.path.abspath(cwd or "."))
        root_fd = open_directory_no_symlinks(root)
        matches: list[SearchMatch] = []
        files_seen: set[str] = set()
        emitted: set[tuple[str, int]] = set()
        files_scanned = 0
        aggregate_bytes = 0
        deadline = time.monotonic() + _SEARCH_TIMEOUT_SECONDS
        try:
            for current_root, directories, filenames, directory_fd in os.fwalk(
                ".",
                topdown=True,
                follow_symlinks=False,
                dir_fd=root_fd,
            ):
                if time.monotonic() >= deadline:
                    return matches, files_seen
                relative_dir = Path(current_root)
                directories[:] = [
                    name
                    for name in directories
                    if _read_path_allowed(root / relative_dir / name)
                ]
                for filename in filenames:
                    if time.monotonic() >= deadline:
                        return matches, files_seen
                    relative_path = relative_dir / filename
                    relative = relative_path.as_posix().removeprefix("./")
                    candidate = root / relative_path
                    if not _read_path_allowed(candidate):
                        continue
                    if file_pattern not in {"", "**/*"} and not (
                        fnmatch.fnmatch(relative, file_pattern)
                        or fnmatch.fnmatch(filename, file_pattern)
                    ):
                        continue
                    files_scanned += 1
                    if files_scanned > _MAX_FILES_SCANNED:
                        return matches, files_seen
                    flags = os.O_RDONLY | os.O_NONBLOCK
                    if hasattr(os, "O_NOFOLLOW"):
                        flags |= os.O_NOFOLLOW
                    try:
                        descriptor = os.open(filename, flags, dir_fd=directory_fd)
                        with os.fdopen(descriptor, "rb") as handle:
                            metadata = os.fstat(handle.fileno())
                            if not stat.S_ISREG(metadata.st_mode):
                                continue
                            size = metadata.st_size
                            if size > _MAX_FILE_BYTES:
                                continue
                            content = handle.read(_MAX_FILE_BYTES + 1)
                            aggregate_bytes += len(content)
                            if aggregate_bytes > _MAX_AGGREGATE_BYTES:
                                return matches, files_seen
                            if len(content) > _MAX_FILE_BYTES:
                                continue
                            lines = (
                                content
                                .decode("utf-8", errors="replace")
                                .splitlines()
                            )
                    except (OSError, UnicodeError):
                        continue
                    if time.monotonic() >= deadline:
                        return matches, files_seen
                    for index, line in enumerate(lines):
                        if time.monotonic() >= deadline:
                            return matches, files_seen
                        bounded_line = line[:_MAX_LINE_LENGTH]
                        try:
                            found = (
                                bool(
                                    matcher.search(
                                        bounded_line,
                                        timeout=_REGEX_TIMEOUT_SECONDS,
                                    )
                                )
                                if matcher
                                else pattern in bounded_line
                            )
                        except TimeoutError:
                            return matches, files_seen
                        if not found:
                            continue
                        files_seen.add(relative)
                        start = max(0, index - context_lines)
                        end = min(len(lines), index + context_lines + 1)
                        for context_index in range(start, end):
                            key = (relative, context_index)
                            if key in emitted:
                                continue
                            emitted.add(key)
                            matches.append(
                                SearchMatch(
                                    relative,
                                    context_index + 1,
                                    lines[context_index][:_MAX_LINE_LENGTH].strip(),
                                )
                            )
                            if len(matches) >= max_results:
                                return matches, files_seen
            return matches, files_seen
        finally:
            os.close(root_fd)

    matches, files_seen = await asyncio.to_thread(search)

    return {
        "matches": [{"file": m.file, "line": m.line, "text": m.text} for m in matches],
        "count": len(matches),
        "files": sorted(files_seen),
        "file_count": len(files_seen),
    }


def _read_path_allowed(path: Path) -> bool:
    """Return whether a traversal candidate passes active read policy."""
    try:
        check_sensitive_read_path(str(path))
    except ValidationError:
        return False
    return True
