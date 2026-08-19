"""Tests for FS Worker tool implementations.

Covers file_read, file_write, and file_search with
path validation, size limits, and security checks.
"""

import os
from pathlib import Path

import pytest

from backend.ipc.protocol import ToolRequest
from backend.tools.validation import ValidationError
from backend.workers.fs_worker import (
    _has_symlink_component,
    _open_for_no_symlink_write,
    _resolve_and_check,
    execute_file_read,
    execute_file_search,
    execute_file_write,
    make_fs_dispatch,
)


class TestFileRead:
    """Tests for execute_file_read."""

    def test_reads_file_content(self, tmp_path):
        """Verify file content is returned."""
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = execute_file_read(str(f), [str(tmp_path)])
        assert result["content"] == "hello world"

    def test_path_traversal_blocked(self, tmp_path):
        """Verify path traversal is rejected."""
        with pytest.raises(ValidationError, match="traversal"):
            execute_file_read(
                str(tmp_path / ".." / "etc" / "passwd"),
                [str(tmp_path)],
            )

    def test_outside_allowed_dirs(self, tmp_path):
        """Verify path outside allowed dirs rejected."""
        f = tmp_path / "test.txt"
        f.write_text("data")
        with pytest.raises(ValidationError, match="outside allowed"):
            execute_file_read(str(f), ["/some/other/dir"])

    def test_large_file_error(self, tmp_path):
        """Verify error for files over 1MB."""
        f = tmp_path / "big.txt"
        f.write_bytes(b"x" * 1_048_577)
        result = execute_file_read(str(f), [str(tmp_path)])
        assert "error" in result
        assert "too large" in result["error"].lower()

    def test_read_through_symlink_blocked(self, tmp_path):
        """Reject a symlink before opening a readable target."""
        real = tmp_path / "real.txt"
        real.write_text("secret")
        link = tmp_path / "link.txt"
        link.symlink_to(real)

        with pytest.raises(ValidationError, match="symlink"):
            execute_file_read(str(link), [str(tmp_path)])


class TestFileWrite:
    """Tests for execute_file_write."""

    def test_writes_content(self, tmp_path):
        """Verify content is written to file."""
        path = str(tmp_path / "output.txt")
        result = execute_file_write(path, "hello", [str(tmp_path)])
        assert result["bytes_written"] == 5
        with open(path) as fh:
            assert fh.read() == "hello"

    def test_creates_parent_dirs(self, tmp_path):
        """Verify parent directories are created."""
        path = str(tmp_path / "sub" / "dir" / "file.txt")
        result = execute_file_write(path, "data", [str(tmp_path)])
        assert result["bytes_written"] == 4
        assert os.path.exists(path)

    def test_sensitive_path_blocked(self, tmp_path):
        """Verify writes to .ssh are blocked."""
        with pytest.raises(ValidationError, match="sensitive"):
            execute_file_write(
                str(tmp_path / ".ssh" / "config"),
                "bad data",
                [str(tmp_path)],
            )

    def test_dotenv_blocked(self, tmp_path):
        """Verify writes to .env are blocked."""
        with pytest.raises(ValidationError, match="sensitive"):
            execute_file_write(
                str(tmp_path / ".env"),
                "SECRET=bad",
                [str(tmp_path)],
            )

    def test_path_traversal_blocked(self, tmp_path):
        """Verify path traversal is rejected."""
        with pytest.raises(ValidationError, match="traversal"):
            execute_file_write(
                str(tmp_path / ".." / "outside.txt"),
                "data",
                [str(tmp_path)],
            )


class TestFileSearch:
    """Tests for execute_file_search."""

    def test_finds_matching_files(self, tmp_path):
        """Verify glob pattern matches files."""
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        (tmp_path / "c.md").write_text("c")
        result = execute_file_search(str(tmp_path), "*.txt", [str(tmp_path)])
        assert len(result["matches"]) == 2

    def test_recursive_search(self, tmp_path):
        """Verify recursive glob works."""
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.txt").write_text("d")
        (tmp_path / "top.txt").write_text("t")
        result = execute_file_search(str(tmp_path), "**/*.txt", [str(tmp_path)])
        assert len(result["matches"]) == 2

    def test_recursive_search_excludes_sensitive_paths(self, tmp_path):
        """Recursive discovery cannot reveal private-key or environment paths."""
        (tmp_path / ".env").write_text("TOKEN=secret")
        ssh = tmp_path / ".ssh"
        ssh.mkdir()
        (ssh / "id_rsa").write_text("private")
        (tmp_path / "public.txt").write_text("public")

        result = execute_file_search(str(tmp_path), "**/*", [str(tmp_path)])

        assert result["matches"] == [str(tmp_path / "public.txt")]

    def test_max_results_limited(self, tmp_path):
        """Verify results capped at 100."""
        for i in range(150):
            (tmp_path / f"file_{i:03d}.txt").write_text(str(i))
        result = execute_file_search(str(tmp_path), "*.txt", [str(tmp_path)])
        assert len(result["matches"]) == 100

    def test_outside_allowed_dirs(self, tmp_path):
        """Verify directory outside allowed rejected."""
        with pytest.raises(ValidationError, match="outside allowed"):
            execute_file_search(str(tmp_path), "*.txt", ["/other/dir"])

    def test_no_matches(self, tmp_path):
        """Verify empty results for no matches."""
        result = execute_file_search(str(tmp_path), "*.xyz", [str(tmp_path)])
        assert result["matches"] == []

    def test_search_through_symlink_blocked(self, tmp_path):
        """Reject a symlinked search root."""
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)

        with pytest.raises(ValidationError, match="symlink"):
            execute_file_search(str(link), "*.txt", [str(tmp_path)])


class TestResolveAndCheck:
    """Tests for _resolve_and_check path validation."""

    def test_prefix_confusion_blocked(self, tmp_path):
        """Verify /workspace-evil does not match /workspace via relative_to."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        evil = tmp_path / "workspace-evil"
        evil.mkdir()
        target = evil / "secret.txt"
        target.write_text("secret")

        with pytest.raises(ValidationError, match="outside allowed"):
            _resolve_and_check(str(target), [str(workspace)])

    def test_file_inside_allowed_ok(self, tmp_path):
        """Verify file inside allowed dir returns resolved Path."""
        f = tmp_path / "data.txt"
        f.write_text("ok")
        result = _resolve_and_check(str(f), [str(tmp_path)])
        assert result == f.resolve()
        assert isinstance(result, Path)

    def test_nested_path_allowed(self, tmp_path):
        """Verify deeply nested path inside allowed dir is accepted."""
        sub = tmp_path / "a" / "b" / "c"
        sub.mkdir(parents=True)
        f = sub / "file.txt"
        f.write_text("nested")
        result = _resolve_and_check(str(f), [str(tmp_path)])
        assert result == f.resolve()


class TestHasSymlinkComponent:
    """Tests for _has_symlink_component."""

    def test_no_symlink_returns_false(self, tmp_path):
        """Verify regular path returns False."""
        f = tmp_path / "file.txt"
        f.write_text("data")
        assert not _has_symlink_component(str(f))

    def test_file_symlink_detected(self, tmp_path):
        """Verify symlink to a file is detected."""
        real = tmp_path / "real.txt"
        real.write_text("data")
        link = tmp_path / "link.txt"
        link.symlink_to(real)
        assert _has_symlink_component(str(link))

    def test_dir_symlink_detected(self, tmp_path):
        """Verify symlink in a directory component is detected."""
        real_dir = tmp_path / "realdir"
        real_dir.mkdir()
        link_dir = tmp_path / "linkdir"
        link_dir.symlink_to(real_dir)
        target = link_dir / "file.txt"
        (real_dir / "file.txt").write_text("data")
        assert _has_symlink_component(str(target))


class TestSymlinkWriteBlocked:
    """Tests for symlink rejection in execute_file_write."""

    def test_write_through_symlink_blocked(self, tmp_path):
        """Verify write to a symlink path raises ValidationError."""
        real = tmp_path / "real.txt"
        real.write_text("original")
        link = tmp_path / "link.txt"
        link.symlink_to(real)

        with pytest.raises(ValidationError, match="symlink"):
            execute_file_write(str(link), "malicious", [str(tmp_path)])

    def test_write_regular_file_allowed(self, tmp_path):
        """Verify write to a regular path is allowed."""
        target = tmp_path / "output.txt"
        result = execute_file_write(str(target), "hello", [str(tmp_path)])
        assert result.get("bytes_written") == 5

    def test_no_symlink_open_rejects_final_symlink(self, tmp_path):
        """Verify open-time symlink rejection closes the TOCTOU gap."""
        real = tmp_path / "real.txt"
        real.write_text("original")
        link = tmp_path / "link.txt"
        link.symlink_to(real)

        with (
            pytest.raises(ValidationError, match="symlink"),
            _open_for_no_symlink_write(link),
        ):
            pass


class TestFsDispatchValidation:
    """Tests for raw FS worker parameter validation."""

    @pytest.mark.asyncio
    async def test_file_read_rejects_non_string_path(self, tmp_path):
        """Verify malformed file_read params return a worker error."""
        handler = make_fs_dispatch([str(tmp_path)])
        request = ToolRequest(
            request_id="req-1",
            tool_name="file_read",
            parameters={"path": 123},
        )

        response = await handler(request)

        assert response.status == "error"
        assert "'path' must be a string" in (response.error_message or "")

    @pytest.mark.asyncio
    async def test_file_write_rejects_non_string_content(self, tmp_path):
        """Verify malformed file_write params return a worker error."""
        handler = make_fs_dispatch([str(tmp_path)])
        request = ToolRequest(
            request_id="req-1",
            tool_name="file_write",
            parameters={"path": str(tmp_path / "out.txt"), "content": ["bad"]},
        )

        response = await handler(request)

        assert response.status == "error"
        assert "'content' must be a string" in (response.error_message or "")

    @pytest.mark.asyncio
    async def test_file_search_rejects_missing_pattern(self, tmp_path):
        """Verify malformed file_search params return a worker error."""
        handler = make_fs_dispatch([str(tmp_path)])
        request = ToolRequest(
            request_id="req-1",
            tool_name="file_search",
            parameters={"directory": str(tmp_path)},
        )

        response = await handler(request)

        assert response.status == "error"
        assert "'pattern' must be a string" in (response.error_message or "")
