"""Behaviour contract tests for the chat file helpers (F27).

The pure seams of the chat-input preparation: the metadata-only prompt
block and its input wrapper, inline image validation, the file metadata
map (including its recoverable failure mode), stored-file resolution,
and the collection that skips unknown ids and survives per-file I/O
errors.
"""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.processes.fastapi_app.routes.chat_file_helpers import (
    METADATA_ONLY_MODE,
    build_metadata_only_file_block,
    build_metadata_only_file_input,
    load_file_metadata_map,
    read_path_bytes,
    read_path_text,
    resolve_stored_chat_file,
    resolve_stored_chat_file_from_collection,
    validate_request_images,
)
from backend.processes.fastapi_app.routes.chat_models import (
    StoredChatFile,
)


def _stored_file(**overrides: object) -> StoredChatFile:
    """Build a stored-file descriptor with the given overrides."""
    base: dict = {
        "file_id": "f1",
        "original_name": "report.pdf",
        "mime_type": "application/pdf",
        "size_bytes": 1234,
        "file_path": Path("/uploads/f1.pdf"),
    }
    base.update(overrides)
    return StoredChatFile(**base)  # type: ignore[arg-type]


class TestBuildMetadataOnlyFileBlock:
    """The prompt block for a file whose text cannot be extracted."""

    def test_contains_name_mode_mime_and_size(self):
        """All four metadata fields are present in the block."""
        block = build_metadata_only_file_block(
            file_name="scan.pdf",
            mime_type="application/pdf",
            size_bytes=42,
        )
        assert "scan.pdf" in block
        assert METADATA_ONLY_MODE in block
        assert "application/pdf" in block
        assert "42 bytes" in block

    def test_default_warning_is_about_unavailable_text_extraction(self):
        """The default warning explains that no text was extracted."""
        block = build_metadata_only_file_block(
            file_name="scan.pdf",
            mime_type="application/pdf",
            size_bytes=42,
        )
        assert "text extraction is not available" in block

    def test_custom_warning_is_used(self):
        """A caller-supplied warning replaces the default text."""
        block = build_metadata_only_file_block(
            file_name="scan.pdf",
            mime_type="application/pdf",
            size_bytes=42,
            warning="custom warning",
        )
        assert block.endswith("custom warning")


class TestValidateRequestImages:
    """Inline image payloads from the chat request."""

    def test_valid_png_data_url_passes(self):
        """A well-formed image data URL is accepted."""
        payload = base64.b64encode(b"\x89PNG").decode()
        validate_request_images([f"data:image/png;base64,{payload}"])

    def test_non_data_url_is_422(self):
        """A plain URL that is not a data URL is a 422."""
        with pytest.raises(HTTPException) as excinfo:
            validate_request_images(["https://example.com/pic.png"])
        assert excinfo.value.status_code == 422

    def test_non_image_mime_is_422(self):
        """A data URL with a non-image MIME type is a 422."""
        payload = base64.b64encode(b"x").decode()
        with pytest.raises(HTTPException) as excinfo:
            validate_request_images([f"data:text/plain;base64,{payload}"])
        assert excinfo.value.status_code == 422


class TestReadPathHelpers:
    """The thread-offloaded file reads."""

    async def test_read_path_bytes_returns_content(self, tmp_path: Path):
        """Bytes come back unchanged."""
        target = tmp_path / "blob.bin"
        target.write_bytes(b"\x00\x01\xff")
        assert await read_path_bytes(target) == b"\x00\x01\xff"

    async def test_read_path_text_replaces_invalid_utf8(self, tmp_path: Path):
        """Invalid UTF-8 bytes do not raise; they are replaced."""
        target = tmp_path / "mixed.txt"
        target.write_bytes(b"ok\xffbad")
        text = await read_path_text(target)
        assert "ok" in text
        assert "bad" in text
        assert "\ufffd" in text


class TestLoadFileMetadataMap:
    """The file metadata lookup behind the helpers."""

    async def test_returns_files_keyed_by_id(self):
        """Only dict entries with an id survive, keyed by string id."""
        admin = SimpleNamespace(
            send_operation=lambda op: _FixedResponse(
                {
                    "files": [
                        {"id": "f1", "original_name": "a.txt"},
                        {"no_id": True},
                        "junk",
                        {"id": 7, "original_name": "b.txt"},
                    ]
                }
            )
        )
        result = await load_file_metadata_map(admin)
        assert set(result) == {"f1", "7"}

    async def test_recoverable_failure_returns_empty_map(self):
        """A failing file listing degrades to no files, not an error."""

        async def _raise(op: str):
            raise OSError("ipc down")

        admin = SimpleNamespace(send_operation=_raise)
        assert await load_file_metadata_map(admin) == {}


class _FixedResponse:
    """Awaitable carrying a canned operation response."""

    def __init__(self, response: dict) -> None:
        self.response = response

    def __await__(self):
        async def _coroutine():
            return self.response

        return _coroutine().__await__()


class TestResolveStoredChatFile:
    """Building the stored-file descriptor from metadata."""

    def test_builds_descriptor_with_extension_in_path(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The file path joins the uploads dir with the file id.

        The id is suffixed with the lower-cased original-name extension.
        """
        monkeypatch.setattr(
            "backend.files.paths.uploaded_files_dir",
            lambda: Path("/tmp/uploads"),
        )
        resolved = resolve_stored_chat_file(
            file_id="f9",
            file_metadata={
                "original_name": "My Report.PDF",
                "mime_type": "application/pdf",
                "size": "2048",
            },
        )
        assert resolved.file_id == "f9"
        assert resolved.original_name == "My Report.PDF"
        assert resolved.size_bytes == 2048
        assert resolved.file_path == Path("/tmp/uploads/f9.pdf")

    def test_missing_mime_and_size_default(self, monkeypatch: pytest.MonkeyPatch):
        """Absent mime type and size become empty string and zero."""
        monkeypatch.setattr(
            "backend.files.paths.uploaded_files_dir",
            lambda: Path("/tmp/uploads"),
        )
        resolved = resolve_stored_chat_file(
            file_id="f2",
            file_metadata={"original_name": "noext"},
        )
        assert resolved.mime_type == ""
        assert resolved.size_bytes == 0
        assert resolved.file_path == Path("/tmp/uploads/f2")


class TestBuildMetadataOnlyFileInput:
    """The prepared input for a non-text file."""

    def test_single_block_and_labeled_resolution(self):
        """One prompt block plus a ``name [metadata_only]`` label."""
        prepared = build_metadata_only_file_input(stored_file=_stored_file())
        assert len(prepared.prompt_blocks) == 1
        assert "report.pdf" in prepared.prompt_blocks[0]
        assert prepared.images == []
        assert prepared.resolved_files == ["report.pdf [metadata_only]"]


class TestResolveFromCollection:
    """Resolving one attached id against the metadata map."""

    def test_unknown_id_is_none(self):
        """An id missing from the map resolves to nothing."""
        assert (
            resolve_stored_chat_file_from_collection(
                file_id="ghost",
                id_to_file={},
            )
            is None
        )

    def test_known_id_is_resolved(self, monkeypatch: pytest.MonkeyPatch):
        """A present id becomes a stored-file descriptor."""
        monkeypatch.setattr(
            "backend.files.paths.uploaded_files_dir",
            lambda: Path("/tmp/uploads"),
        )
        resolved = resolve_stored_chat_file_from_collection(
            file_id="f1",
            id_to_file={"f1": {"original_name": "a.txt", "mime_type": "text/plain"}},
        )
        assert resolved is not None
        assert resolved.original_name == "a.txt"
