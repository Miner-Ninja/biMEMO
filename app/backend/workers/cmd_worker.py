"""Command execution worker — runs shell commands in an isolated subprocess.

Provides execute_command (one-shot) and execute_command_stream (real-time
output) with allowlist-based security and configurable timeout.

Security:
- Only whitelisted commands may execute (checked via shutil.which + basename)
- Working directory is set to workspace root
- Hard timeout kills the process
- No pty — pure pipe-based I/O
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


def _open_working_directory(path: str) -> tuple[int, str]:
    """Pin a working directory while rejecting every symlink component."""
    absolute = Path(os.path.abspath(Path(path).expanduser()))
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    current_fd = os.open(absolute.anchor or "/", flags)
    try:
        for part in absolute.parts[1:]:
            next_fd = os.open(part, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd, f"/proc/self/fd/{current_fd}"
    except Exception:
        os.close(current_fd)
        raise


# ---------------------------------------------------------------------------
# Allowlist / Blocklist
# ---------------------------------------------------------------------------

ALLOWED_COMMANDS: frozenset[str] = frozenset(
    {
        # Navigation
        "ls",
        "cd",
        "pwd",
        "tree",
        "find",
        # File ops (read-only)
        "cat",
        "head",
        "tail",
        "less",
        "more",
        "wc",
        "stat",
        # Search
        "grep",
        "rg",  # ripgrep
        # Text processing
        "sed",
        "awk",
        "sort",
        "uniq",
        "diff",
        "patch",
        # Dev tools
        "python",
        "python3",
        "node",
        "npm",
        "npx",
        "pip",
        "pip3",
        "git",
        "make",
        "cmake",
        "cargo",
        "go",
        "rustc",
        "docker",
        "docker-compose",
        # System info
        "uname",
        "env",
        "printenv",
        "echo",
        "which",
        "whereis",
        # Compression
        "tar",
        "gzip",
        "gunzip",
        "zip",
        "unzip",
        # Editors (non-interactive usage via stdin)
        "nano",
        "vim",
        "vi",
        "nvim",
        # Monitoring
        "top",
        "htop",
        "ps",
        "df",
        "du",
        "free",
        # Misc
        "mkdir",
        "cp",
        "mv",
        "touch",
        "ln",
        "basename",
        "dirname",
        "tr",
        "cut",
        "paste",
        "join",
        "comm",
        "shuf",
        "seq",
        "date",
        "sleep",
        "true",
        "false",
        "test",
        "[",
        "curl",
        "wget",  # network — allowed but RED zone
        "java",
        "javac",
        "kotlin",
        "kotlinc",
        "ruby",
        "gem",
        "bundle",
        "php",
        "composer",
        "perl",
        "lua",
        "r",
        "R",
        "swift",
        "swiftc",
        "rustup",
    }
)

BLOCKED_COMMANDS: frozenset[str] = frozenset(
    {
        "rm",
        "rmdir",
        "chmod",
        "chown",
        "chgrp",
        "sudo",
        "su",
        "ssh",
        "scp",
        "sftp",
        "mkfs",
        "mkfs.ext4",
        "mkfs.fat",
        "mkfs.ntfs",
        "dd",
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
        "kill",
        "killall",
        "pkill",
        "iptables",
        "ufw",
        "firewall-cmd",
        "visudo",
        "visudo.check",
    }
)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _resolve_command(cmd_name: str) -> str | None:
    """Resolve a command name to its absolute path.

    Returns None if the command is not found or is blocked.
    """
    basename = Path(cmd_name).name  # strip any path component
    if basename in BLOCKED_COMMANDS:
        return None
    if basename not in ALLOWED_COMMANDS:
        return None
    return shutil.which(basename)


def validate_command(command: str) -> tuple[str | None, list[str]]:
    """Validate and parse a shell command string.

    Returns (resolved_path, args) or (None, []) if invalid.

    For simplicity, we split on whitespace. Complex shell features
    (pipes, redirects) are not supported — the agent should use
    individual commands instead.
    """
    import shlex

    try:
        parts = shlex.split(command)
    except ValueError as e:
        return None, [f"Failed to parse command: {e}"]

    if not parts:
        return None, ["Empty command"]

    cmd_path = _resolve_command(parts[0])
    if cmd_path is None:
        basename = Path(parts[0]).name
        if basename in BLOCKED_COMMANDS:
            return None, [f"Command '{basename}' is blocked"]
        return None, [f"Command '{parts[0]}' is not allowed"]

    return cmd_path, parts[1:]


# ---------------------------------------------------------------------------
# Execute one-shot command
# ---------------------------------------------------------------------------


@dataclass
class CommandResult:
    """Result of a command execution."""

    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    error: str | None = None


async def execute_command(
    command: str,
    cwd: str | None = None,
    timeout: float = 30.0,  # noqa: ASYNC109 - public execution timeout API
    env_extra: dict[str, str] | None = None,
) -> CommandResult:
    """Run a single command and return its output.

    Args:
        command: Shell command string.
        cwd: Working directory (defaults to workspace root).
        timeout: Max execution time in seconds.
        env_extra: Additional environment variables.

    Returns:
        CommandResult with stdout, stderr, exit_code.
    """
    resolved, args = validate_command(command)
    if resolved is None:
        return CommandResult(
            stdout="",
            stderr="",
            exit_code=1,
            error=f"Command not allowed: {args[0] if args else command}",
        )

    # Build environment
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)

    work_dir = cwd or str(Path.home())
    try:
        work_dir_fd, pinned_work_dir = _open_working_directory(work_dir)
    except OSError:
        return CommandResult(
            stdout="",
            stderr="",
            exit_code=1,
            error=f"Working directory is invalid or contains a symlink: {work_dir}",
        )

    full_cmd = [resolved, *args]

    try:
        try:
            process = await asyncio.create_subprocess_exec(
                *full_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=pinned_work_dir,
                env=env,
                start_new_session=True,  # process isolation
                pass_fds=(work_dir_fd,),
            )
        finally:
            os.close(work_dir_fd)

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except TimeoutError:
            # Kill the process group
            try:
                import signal

                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                process.kill()
            await process.wait()
            return CommandResult(
                stdout="",
                stderr="",
                exit_code=-1,
                timed_out=True,
                error=f"Command timed out after {timeout}s",
            )

        return CommandResult(
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            exit_code=process.returncode or 0,
        )

    except FileNotFoundError:
        return CommandResult(
            stdout="",
            stderr="",
            exit_code=1,
            error=f"Command not found: {resolved}",
        )
    except Exception as e:
        return CommandResult(
            stdout="",
            stderr="",
            exit_code=1,
            error=f"Execution error: {e}",
        )


# ---------------------------------------------------------------------------
# Execute streaming command
# ---------------------------------------------------------------------------


class CommandStream:
    """Streams command output line-by-line via a callback.

    Used by execute_command_stream to push output chunks
    back to the caller in real-time.
    """

    def __init__(
        self,
        on_chunk: Callable[[str, str], None],
    ) -> None:
        """Initialize the stream.

        Args:
            on_chunk: Callback(stdout_line, stderr_line) called for each line.
        """
        self._on_chunk = on_chunk
        self._result: CommandResult | None = None
        self._cancelled = False

    @property
    def result(self) -> CommandResult | None:
        """Return the completed command result, if available."""
        return self._result

    @property
    def is_done(self) -> bool:
        """Return whether the streaming command has completed."""
        return self._result is not None

    async def run(
        self,
        command: str,
        cwd: str | None = None,
        timeout: float = 30.0,  # noqa: ASYNC109 - public execution timeout API
        env_extra: dict[str, str] | None = None,
    ) -> CommandResult:
        """Start the streaming command execution.

        Returns CommandResult when the command finishes or times out.
        """
        resolved, args = validate_command(command)
        if resolved is None:
            result = CommandResult(
                stdout="",
                stderr="",
                exit_code=1,
                error=f"Command not allowed: {args[0] if args else command}",
            )
            self._result = result
            return result

        env = os.environ.copy()
        if env_extra:
            env.update(env_extra)

        work_dir = cwd or str(Path.home())
        try:
            work_dir_fd, pinned_work_dir = _open_working_directory(work_dir)
        except OSError:
            result = CommandResult(
                stdout="",
                stderr="",
                exit_code=1,
                error=(
                    f"Working directory is invalid or contains a symlink: {work_dir}"
                ),
            )
            self._result = result
            return result

        full_cmd = [resolved, *args]

        try:
            try:
                process = await asyncio.create_subprocess_exec(
                    *full_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=pinned_work_dir,
                    env=env,
                    start_new_session=True,
                    pass_fds=(work_dir_fd,),
                )
            finally:
                os.close(work_dir_fd)
        except FileNotFoundError:
            result = CommandResult(
                stdout="",
                stderr="",
                exit_code=1,
                error=f"Command not found: {resolved}",
            )
            self._result = result
            return result
        except Exception as e:
            result = CommandResult(
                stdout="",
                stderr="",
                exit_code=1,
                error=f"Execution error: {e}",
            )
            self._result = result
            return result

        stdout_buf = []
        stderr_buf = []

        async def read_stream(
            stream: asyncio.StreamReader | None,
            buf: list[str],
            stream_name: str,
        ) -> None:
            """Read lines from a stream and push them via on_chunk."""
            if stream is None:
                return
            while not self._cancelled:
                try:
                    line = await asyncio.wait_for(stream.readline(), timeout=1.0)
                except TimeoutError:
                    continue
                if not line:
                    break
                text = line.decode("utf-8", errors="replace")
                buf.append(text)
                other = "" if stream_name == "stdout" else text
                other_arg = text if stream_name == "stdout" else ""
                self._on_chunk(other_arg, other)

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    read_stream(process.stdout, stdout_buf, "stdout"),
                    read_stream(process.stderr, stderr_buf, "stderr"),
                ),
                timeout=timeout,
            )
            exit_code = process.returncode or 0
        except TimeoutError:
            self._cancelled = True
            try:
                import signal

                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                process.kill()
            await process.wait()
            exit_code = -1

        result = CommandResult(
            stdout="".join(stdout_buf),
            stderr="".join(stderr_buf),
            exit_code=exit_code,
            timed_out=exit_code == -1,
            error=f"Timed out after {timeout}s" if exit_code == -1 else None,
        )
        self._result = result
        return result

    def cancel(self) -> None:
        """Signal cancellation to stop reading."""
        self._cancelled = True


# ---------------------------------------------------------------------------
# IPC dispatch (for subprocess worker)
# ---------------------------------------------------------------------------


def make_cmd_dispatch() -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create the dispatch function for the cmd_worker IPC handler.

    Handles:
    - execute_command: one-shot execution
    - execute_command_stream: streaming (not supported via sync IPC,
      returns error — use orchestrator-level streaming instead)
    """

    def dispatch(params: dict[str, Any]) -> dict[str, Any]:
        operation = params.get("operation", "")
        command = params.get("command", "")
        cwd = params.get("cwd")
        timeout = params.get("timeout", 30.0)
        env_extra = params.get("env_extra")

        if operation == "execute_command":
            # Run synchronously in the worker process
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    execute_command(
                        command=command,
                        cwd=cwd,
                        timeout=timeout,
                        env_extra=env_extra,
                    )
                )
            finally:
                loop.close()
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "error": result.error,
            }

        if operation == "execute_command_stream":
            # Streaming requires async IPC — not supported in worker dispatch.
            # The orchestrator handles streaming directly.
            return {
                "error": "Streaming not supported via worker IPC. "
                "Use orchestrator-level execute_command_stream.",
            }

        return {"error": f"Unknown operation: {operation}"}

    return dispatch
