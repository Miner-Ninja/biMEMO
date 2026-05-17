"""Filesystem worker tool implementations.

Executes file_read, file_write, and file_search within the
FS Worker subprocess. All paths are validated against allowed
directories and security checks before execution.

Provides the subprocess entry point (fs_worker_main) and the
Core-side dispatch handler factory (make_fs_dispatch).
"""

from __future__ import annotations

import errno
import glob
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from backend.tools.validation import (
    ValidationError,
    check_path_traversal,
    check_sensitive_path,
)


logger = logging.getLogger(__name__)

MAX_READ_SIZE = 1_048_576  # 1 MB
MAX_SEARCH_RESULTS = 100
FILE_WRITE_MODE = 0o644

if TYPE_CHECKING:
    import multiprocessing.connection

    from backend.ipc.protocol import ToolRequest, ToolResponse


def _tool_success_response(request_id: str, result: dict) -> ToolResponse:
    """Build a standard successful worker response."""
    from backend.ipc.protocol import ToolResponse

    return ToolResponse(
        request_id=request_id,
        status="success",
        result=result,
    )


def _tool_error_response(request_id: str, message: str) -> ToolResponse:
    """Build a standard failed worker response."""
    from backend.ipc.protocol import ToolResponse

    return ToolResponse(
        request_id=request_id,
        status="error",
        error_message=message,
    )


def _unknown_tool_response(request_id: str, tool_name: str) -> ToolResponse:
    """Build the standard unknown-tool response for FS worker dispatch."""
    return _tool_error_response(request_id, f"Unknown tool: {tool_name}")


def _resolve_and_check(path: str, allowed_dirs: list[str]) -> Path:
    """Resolve a path and verify it stays within allowed dirs.

    Uses Path.relative_to() to prevent prefix-match false positives
    (e.g. /workspace-evil matching /workspace).

    Args:
        path: File path to resolve.
        allowed_dirs: List of allowed parent directories.

    Returns:
        Resolved absolute Path object.

    Raises:
        ValidationError: If path is outside allowed directories.
    """
    check_path_traversal(path)
    resolved = Path(path).resolve()
    for allowed in allowed_dirs:
        allowed_resolved = Path(allowed).resolve()
        try:
            resolved.relative_to(allowed_resolved)
            return resolved
        except ValueError:
            continue
    raise ValidationError("Path outside allowed directories")


def _has_symlink_component(path: str) -> bool:
    """Return True if any existing component of path is a symlink.

    Walks path components from root down, checking is_symlink()
    at each existing prefix so symlinks inside allowed dirs are detected.

    Args:
        path: Absolute or relative path string.

    Returns:
        True if any path component is a symlink.
    """
    p = Path(path)
    check = Path(p.anchor) if p.is_absolute() else Path.cwd()
    parts = p.parts[1:] if p.is_absolute() else p.parts
    for part in parts:
        check = check / part
        if check.is_symlink():
            return True
    return False


def _required_string_param(params: dict, name: str) -> str:
    """Return a required string parameter from raw worker params."""
    value = params.get(name)
    if not isinstance(value, str):
        raise ValidationError(f"'{name}' must be a string")
    return value


def _file_read_params(params: dict) -> tuple[str]:
    """Validate raw file_read worker params."""
    return (_required_string_param(params, "path"),)


def _file_write_params(params: dict) -> tuple[str, str]:
    """Validate raw file_write worker params."""
    return (
        _required_string_param(params, "path"),
        _required_string_param(params, "content"),
    )


def _file_search_params(params: dict) -> tuple[str, str]:
    """Validate raw file_search worker params."""
    return (
        _required_string_param(params, "directory"),
        _required_string_param(params, "pattern"),
    )


def _open_for_no_symlink_write(path: Path) -> BinaryIO:
    """Open a file for writing while atomically rejecting symlink targets."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags, FILE_WRITE_MODE)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise ValidationError("Write through symlink is not allowed") from error
        raise
    try:
        return os.fdopen(fd, "wb")
    except Exception:
        os.close(fd)
        raise


def execute_file_read(path: str, allowed_dirs: list[str]) -> dict:
    """Read a file within allowed directories.

    Args:
        path: File path to read.
        allowed_dirs: List of allowed parent directories.

    Returns:
        Dict with content string.

    Raises:
        ValidationError: If path is invalid.
    """
    resolved = _resolve_and_check(path, allowed_dirs)

    size = os.path.getsize(resolved)
    if size > MAX_READ_SIZE:
        return {"error": (f"File too large: {size} bytes (max {MAX_READ_SIZE})")}

    with open(resolved, encoding="utf-8", errors="replace") as f:
        content = f.read()
    return {"content": content}


def execute_file_write(
    path: str,
    content: str,
    allowed_dirs: list[str],
) -> dict:
    """Write content to a file within allowed directories.

    Args:
        path: File path to write.
        content: Content to write.
        allowed_dirs: List of allowed parent directories.

    Returns:
        Dict with bytes_written count.

    Raises:
        ValidationError: If path is invalid, sensitive, or via symlink.
    """
    check_sensitive_path(path)
    if _has_symlink_component(path):
        raise ValidationError("Write through symlink is not allowed")
    resolved = _resolve_and_check(path, allowed_dirs)

    os.makedirs(resolved.parent, exist_ok=True)

    encoded = content.encode("utf-8")
    with _open_for_no_symlink_write(resolved) as f:
        written = f.write(encoded)
    return {"bytes_written": written}


def execute_file_search(
    directory: str,
    pattern: str,
    allowed_dirs: list[str],
) -> dict:
    """Search for files matching a glob pattern.

    Args:
        directory: Directory to search in.
        pattern: Glob pattern to match.
        allowed_dirs: List of allowed parent directories.

    Returns:
        Dict with list of matching paths.

    Raises:
        ValidationError: If directory is outside allowed dirs.
    """
    resolved_dir = _resolve_and_check(directory, allowed_dirs)

    check_path_traversal(pattern)
    full_pattern = str(resolved_dir / pattern)
    matches = sorted(glob.glob(full_pattern, recursive=True))

    return {"matches": matches[:MAX_SEARCH_RESULTS]}


def fs_worker_main(
    socket_path: str,
    conn: multiprocessing.connection.Connection,
) -> None:
    """Subprocess entry point for the FS Worker.

    Reads the shared secret from the parent process via pipe,
    then blocks until terminated by the parent.

    Args:
        socket_path: Unix socket path (unused, managed by Core).
        conn: Pipe connection for receiving the shared secret.
    """
    from backend.workers.common import worker_main

    worker_main(conn)


def make_fs_dispatch(allowed_dirs: list[str]) -> callable:
    """Create a dispatch handler for FS Worker tool requests.

    Returns an async handler that routes ToolRequest messages
    to the appropriate file operation handler.

    Args:
        allowed_dirs: List of allowed parent directories.

    Returns:
        Async handler function for ToolRequest dispatch.
    """
    _dispatch = {
        "file_read": lambda p: execute_file_read(*_file_read_params(p), allowed_dirs),
        "file_write": lambda p: execute_file_write(
            *_file_write_params(p),
            allowed_dirs,
        ),
        "file_search": lambda p: execute_file_search(
            *_file_search_params(p),
            allowed_dirs,
        ),
    }

    async def handler(request: ToolRequest) -> ToolResponse:
        """Dispatch a tool request to the appropriate handler.

        Args:
            request: Tool execution request.

        Returns:
            Tool execution response.
        """
        fn = _dispatch.get(request.tool_name)
        if fn is None:
            return _unknown_tool_response(request.request_id, request.tool_name)
        try:
            result = fn(request.parameters)
            return _tool_success_response(request.request_id, result)
        except Exception as e:
            return _tool_error_response(request.request_id, str(e))

    return handler
