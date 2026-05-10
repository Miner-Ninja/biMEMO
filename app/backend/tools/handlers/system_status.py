"""System status tool handler.

Collects disk, memory, GPU, Ollama, worker, and uptime
metrics without executing shell commands.
"""

import logging
import os
import time
from typing import TYPE_CHECKING

import psutil


if TYPE_CHECKING:
    from backend.tools.builtin import SystemStatusInput


logger = logging.getLogger(__name__)

DB_SIZE_WARNING_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB


def _disk_usage_line(data_dir: str) -> str:
    """Return the formatted disk-usage line."""
    try:
        disk = psutil.disk_usage(data_dir)
        return (
            f"Disk: {_fmt_bytes(disk.used)} / "
            f"{_fmt_bytes(disk.total)} "
            f"({disk.percent}% used)"
        )
    except OSError:
        return "Disk: unavailable"


def _process_memory_line() -> str:
    """Return the formatted process RSS line."""
    process = psutil.Process()
    memory_info = process.memory_info()
    return f"RAM (process): {_fmt_bytes(memory_info.rss)}"


def _system_memory_line() -> str:
    """Return the formatted system memory line."""
    virtual_memory = psutil.virtual_memory()
    return (
        f"RAM (system): {_fmt_bytes(virtual_memory.used)} / "
        f"{_fmt_bytes(virtual_memory.total)} "
        f"({virtual_memory.percent}% used)"
    )


def _database_size_line(db_path: str) -> str:
    """Return the formatted database-size line."""
    try:
        db_size = os.stat(db_path).st_size
    except OSError:
        return "Database: unavailable"

    line = f"Database: {_fmt_bytes(db_size)}"
    if db_size > DB_SIZE_WARNING_BYTES:
        line += " — WARNING: Database size exceeds 2GB threshold"
    return line


def _base_status_lines(
    *,
    data_dir: str,
    get_ollama_status: callable,
    get_worker_count: callable,
) -> list[str]:
    """Return the base system-status lines that are always shown."""
    return [
        _disk_usage_line(data_dir),
        _process_memory_line(),
        _system_memory_line(),
        f"Ollama: {get_ollama_status()}",
        f"Workers: {get_worker_count()}",
    ]


def make_system_status_handler(
    data_dir: str,
    ready_since: float,
    get_ollama_status: callable,
    get_worker_count: callable,
    db_path: str | None = None,
) -> callable:
    """Create a handler for system_status tool.

    Args:
        data_dir: Path to the data volume for disk usage.
        ready_since: Timestamp when the app entered READY.
        get_ollama_status: Callable returning Ollama status string.
        get_worker_count: Callable returning active worker count.
        db_path: Path to the SQLCipher database file.

    Returns:
        Async handler function.
    """

    async def handle_system_status(
        params: SystemStatusInput,
    ) -> str:
        """Collect and format system status metrics.

        Args:
            params: Validated system status input (empty).

        Returns:
            Formatted status string.
        """
        lines = _base_status_lines(
            data_dir=data_dir,
            get_ollama_status=get_ollama_status,
            get_worker_count=get_worker_count,
        )
        if db_path is not None:
            lines.append(_database_size_line(db_path))
        uptime = time.time() - ready_since
        lines.append(f"Uptime: {_fmt_duration(uptime)}")

        return "\n".join(lines)

    return handle_system_status


def _fmt_bytes(n: int) -> str:
    """Format bytes as a human-readable string.

    Args:
        n: Number of bytes.

    Returns:
        Formatted string (e.g., "1.5 GB").
    """
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _fmt_duration(seconds: float) -> str:
    """Format duration as a human-readable string.

    Args:
        seconds: Duration in seconds.

    Returns:
        Formatted string (e.g., "2h 15m").
    """
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    if minutes > 0:
        return f"{minutes}m {int(seconds % 60)}s"
    return f"{int(seconds)}s"
