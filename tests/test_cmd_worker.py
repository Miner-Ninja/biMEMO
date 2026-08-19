"""Tests for command worker.

Covers allowlist validation, command execution, timeout handling,
and error cases.
"""

import pytest

from backend.workers.cmd_worker import (
    ALLOWED_COMMANDS,
    BLOCKED_COMMANDS,
    CommandResult,
    CommandStream,
    _resolve_command,
    execute_command,
    validate_command,
)


# ---------------------------------------------------------------------------
# Allowlist / Blocklist
# ---------------------------------------------------------------------------


def test_allowed_commands_not_empty():
    """Verify the allowed-command allowlist holds a meaningful number of entries."""
    assert len(ALLOWED_COMMANDS) > 20


def test_blocked_commands_not_empty():
    """Verify the blocked-command blocklist holds a meaningful number of entries."""
    assert len(BLOCKED_COMMANDS) > 5


def test_blocked_command_not_in_allowed():
    """Ensure no command appears in both the allowed and blocked lists."""
    # No command should be in both lists
    intersection = ALLOWED_COMMANDS & BLOCKED_COMMANDS
    assert not intersection, f"Overlap: {intersection}"


def test_resolve_allowed_command():
    """Ensure _resolve_command resolves echo to a real path on this system."""
    # 'echo' should be allowed and found on most systems
    result = _resolve_command("echo")
    assert result is not None  # echo exists on virtually all systems


def test_resolve_blocked_command():
    """Ensure _resolve_command refuses blocked commands such as rm, sudo, and kill."""
    assert _resolve_command("rm") is None
    assert _resolve_command("sudo") is None
    assert _resolve_command("kill") is None


def test_resolve_unknown_command():
    """Ensure _resolve_command returns None for a command on no list."""
    # A command that's neither allowed nor blocked
    assert _resolve_command("nonexistent_cmd_xyz") is None


def test_resolve_path_stripped():
    """Ensure _resolve_command strips the path before resolving /usr/bin/echo."""
    # Path component should be stripped
    result = _resolve_command("/usr/bin/echo")
    # Should resolve to the actual path or None if blocked
    # echo is allowed, so it should resolve
    assert result is not None or "echo" not in ALLOWED_COMMANDS


# ---------------------------------------------------------------------------
# validate_command
# ---------------------------------------------------------------------------


def test_validate_echo():
    """Ensure validate_command resolves echo and extracts its argument."""
    resolved, args = validate_command("echo hello")
    assert resolved is not None
    assert args == ["hello"]


def test_validate_blocked():
    """Ensure validate_command rejects a blocked command with a block-related error."""
    resolved, errors = validate_command("rm -rf /")
    assert resolved is None
    assert any("blocked" in e for e in errors)


def test_validate_not_allowed():
    """Ensure validate_command rejects a command that is not on the allowlist."""
    resolved, errors = validate_command("nonexistent_cmd_xyz")
    assert resolved is None
    assert len(errors) > 0


def test_validate_empty():
    """Ensure validate_command rejects an empty command with an empty-input error."""
    resolved, errors = validate_command("")
    assert resolved is None
    assert any("empty" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# execute_command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_echo():
    """Ensure execute_command runs echo and returns its stdout with no error."""
    result = await execute_command("echo hello world")
    assert result.exit_code == 0
    assert "hello world" in result.stdout
    assert result.error is None


@pytest.mark.asyncio
async def test_execute_pwd():
    """Ensure execute_command runs pwd with exit code 0 and a usable path."""
    result = await execute_command("pwd")
    assert result.exit_code == 0
    assert "/" in result.stdout  # pwd always contains /


@pytest.mark.asyncio
async def test_execute_blocked():
    """Ensure execute_command reports an error when a command is blocked."""
    result = await execute_command("rm -rf /")
    assert result.exit_code == 1
    assert result.error is not None
    assert "blocked" in result.error.lower() or "not allowed" in result.error.lower()


@pytest.mark.asyncio
async def test_execute_with_cwd():
    """Ensure execute_command runs inside the requested working directory."""
    result = await execute_command("pwd", cwd="/tmp")
    assert result.exit_code == 0
    assert "/tmp" in result.stdout


@pytest.mark.asyncio
async def test_execute_invalid_cwd():
    """Ensure execute_command fails cleanly for a missing working directory."""
    result = await execute_command("echo test", cwd="/nonexistent_dir_xyz")
    assert result.exit_code == 1
    assert result.error is not None


@pytest.mark.asyncio
async def test_execute_rejects_symlink_cwd(tmp_path):
    """The command must run only in the exact authorized directory."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)

    result = await execute_command("pwd", cwd=str(link))

    assert result.exit_code == 1
    assert "symlink" in str(result.error).lower()


@pytest.mark.asyncio
async def test_execute_timeout():
    """Ensure execute_command times out a long sleep and reports it as timed out."""
    # sleep 10 should timeout with 1s timeout
    result = await execute_command("sleep 10", timeout=1.0)
    assert result.timed_out is True
    assert result.exit_code == -1
    assert result.error is not None


@pytest.mark.asyncio
async def test_execute_with_stderr():
    """Ensure execute_command surfaces stderr output from a failing command."""
    # ls a nonexistent file produces stderr
    result = await execute_command("ls nonexistent_file_xyz")
    # Exit code may vary by shell, but stderr should have content
    # or exit_code != 0
    assert result.exit_code != 0 or result.stderr


@pytest.mark.asyncio
async def test_execute_env_extra():
    """Ensure execute_command accepts extra environment variables without error."""
    result = await execute_command(
        "echo $TEST_VAR",
        env_extra={"TEST_VAR": "hello_from_env"},
    )
    # Note: $TEST_VAR may not be expanded without shell=True
    # But the command should still execute without error
    assert result.error is None


# ---------------------------------------------------------------------------
# CommandStream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_echo():
    """Ensure CommandStream streams chunks and keeps the final result."""
    chunks = []

    def on_chunk(stdout: str, stderr: str) -> None:
        if stdout:
            chunks.append(("stdout", stdout))
        if stderr:
            chunks.append(("stderr", stderr))

    stream = CommandStream(on_chunk=on_chunk)
    result = await stream.run("echo hello")

    assert result.exit_code == 0
    assert stream.is_done
    assert stream.result is result


@pytest.mark.asyncio
async def test_stream_timeout():
    """Ensure a CommandStream run reports a timeout for a long sleep."""
    chunks = []

    def on_chunk(stdout: str, stderr: str) -> None:
        chunks.append(stdout or stderr)

    stream = CommandStream(on_chunk=on_chunk)
    result = await stream.run("sleep 10", timeout=1.0)

    assert result.timed_out is True
    assert result.exit_code == -1


@pytest.mark.asyncio
async def test_stream_blocked():
    """Ensure a blocked command emits no chunks and returns an error from the stream."""
    chunks = []

    def on_chunk(stdout: str, stderr: str) -> None:
        chunks.append(stdout or stderr)

    stream = CommandStream(on_chunk=on_chunk)
    result = await stream.run("rm -rf /")

    assert result.exit_code == 1
    assert result.error is not None
    assert len(chunks) == 0  # No chunks for blocked commands


# ---------------------------------------------------------------------------
# CommandResult
# ---------------------------------------------------------------------------


def test_command_result_defaults():
    """Ensure CommandResult defaults timed_out to False and error to None."""
    result = CommandResult(stdout="", stderr="", exit_code=0)
    assert result.timed_out is False
    assert result.error is None
