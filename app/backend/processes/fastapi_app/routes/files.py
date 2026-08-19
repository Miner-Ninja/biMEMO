"""File management routes (upload, list, delete, read).

Allowed extensions, bare-name allowlist (Makefile, Dockerfile,
...), and the maximum file size are sourced from AssistantPolicy,
so runtime policy changes take effect on the next upload without
restarting the process.
"""

# ruff: noqa: B008

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import uuid
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from backend.files import FileContextManager
from backend.files.paths import UPLOADED_FILES_DIR
from backend.security.policy import get_policy

from ..deps import _get_admin_client, _require_auth
from .etag import if_none_match_matches


if TYPE_CHECKING:
    import os

    from backend.channels.jwt_auth import TokenClaims

    from ..clients import AdminDBClient


logger = logging.getLogger(__name__)

files_router = APIRouter(prefix="/files")

# --- On-disk paths and conversion factors ---
FILES_DIR = UPLOADED_FILES_DIR
BYTES_PER_MEGABYTE = 1024 * 1024
# Cache-Control for downloaded file content (content is immutable per id)
FILE_CONTENT_CACHE_CONTROL = "private, max-age=86400"

# --- Catalog keys read from AssistantPolicy ---
_KEY_ALLOWED_EXTENSIONS = "filesystem.uploads.allowed_extensions"
_KEY_BARE_NAME_ALLOWLIST = "filesystem.uploads.bare_name_allowlist"
_KEY_MAX_SIZE_MB = "filesystem.uploads.max_size_mb"

FILE_CONTEXT_MANAGER = FileContextManager()


def _allowed_extensions() -> frozenset[str]:
    """Return AssistantPolicy's allowlist of file extensions."""
    return frozenset(get_policy().get_list_str(_KEY_ALLOWED_EXTENSIONS))


def _bare_name_allowlist() -> frozenset[str]:
    """Return AssistantPolicy's allowlist of files without extensions."""
    return frozenset(get_policy().get_list_str(_KEY_BARE_NAME_ALLOWLIST))


def _max_file_size_bytes() -> int:
    """Return AssistantPolicy's per-file size limit in bytes."""
    return get_policy().get_int(_KEY_MAX_SIZE_MB) * BYTES_PER_MEGABYTE


def _is_filename_allowed(file_name: str) -> bool:
    """Return whether one filename is accepted by AssistantPolicy."""
    extension = Path(file_name).suffix.lower()
    if extension and extension in _allowed_extensions():
        return True
    return file_name in _bare_name_allowlist()


async def _require_file_metadata(
    admin: AdminDBClient,
    file_id: str,
) -> dict:
    """Load one file metadata record or raise 404."""
    meta = await admin.send_operation("get_file_metadata", {"file_id": file_id})
    file_info = meta.get("file")
    if not file_info:
        raise HTTPException(status_code=404, detail="File not found")
    return file_info


def _file_path(file_id: str, original_name: str) -> Path:
    """Return the on-disk path for one stored file."""
    ext = Path(original_name).suffix.lower()
    return FILES_DIR / f"{file_id}{ext}"


def _file_etag(stat_result: os.stat_result) -> str:
    """Return the quoted ETag one FileResponse emits for a stat result.

    Mirrors starlette 1.6.0 FileResponse.set_stat_headers (md5 of
    mtime + size), so the value compared against If-None-Match is
    exactly the ETag header the FileResponse response carries.
    """
    etag_base = str(stat_result.st_mtime) + "-" + str(stat_result.st_size)
    return f'"{hashlib.md5(etag_base.encode(), usedforsecurity=False).hexdigest()}"'


def _uploaded_file_response(
    *,
    file_id: str,
    file_name: str,
    size_bytes: int,
    mime_type: str,
) -> dict[str, object]:
    """Build the standard upload response body for one stored file."""
    return {
        "id": file_id,
        "original_name": file_name,
        "size": size_bytes,
        "mime_type": mime_type,
    }


@files_router.post("/upload")
async def upload_file(
    file: UploadFile,
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> JSONResponse:
    """Upload a file with type and size validation.

    Returns 201 with the stored file path in Location.

    Args:
        file: Uploaded file.
        _claims: Authenticated user.
        admin: DB Proxy client.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    if not _is_filename_allowed(file.filename):
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {file.filename}",
        )

    max_size_bytes = _max_file_size_bytes()
    content = await file.read()
    if len(content) > max_size_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {len(content)} bytes (max {max_size_bytes})",
        )

    file_id = str(uuid.uuid4())
    mime_type = (
        file.content_type
        or mimetypes.guess_type(file.filename)[0]
        or "application/octet-stream"
    )
    stored_file_context_payload: dict[str, object] | None = None
    if FILE_CONTEXT_MANAGER.is_textual_file(
        file_name=file.filename,
        mime_type=mime_type,
    ):
        text_content = content.decode("utf-8", errors="replace")
        stored_file_context = FILE_CONTEXT_MANAGER.build_stored_file_context(
            file_id=file_id,
            file_name=file.filename,
            mime_type=mime_type,
            content=text_content,
        )
        stored_file_context_payload = (
            FILE_CONTEXT_MANAGER.serialize_stored_file_context(
                stored_context=stored_file_context,
            )
        )

    await asyncio.to_thread(FILES_DIR.mkdir, parents=True, exist_ok=True)
    file_path = _file_path(file_id, file.filename)
    await asyncio.to_thread(file_path.write_bytes, content)
    replacements: list[dict] = []
    metadata_created = False

    try:
        existing_files = await admin.send_operation("list_files")
        replacements = [
            item
            for item in existing_files.get("files", [])
            if item.get("original_name") == file.filename
        ]
        await admin.send_operation(
            "create_file_metadata",
            {
                "id": file_id,
                "original_name": file.filename,
                "size": len(content),
                "mime_type": mime_type,
            },
        )
        metadata_created = True
        if stored_file_context_payload is not None:
            await admin.send_operation(
                "save_file_context",
                stored_file_context_payload,
            )
        for old_file in replacements:
            old_path = _file_path(old_file["id"], old_file["original_name"])
            old_path.unlink(missing_ok=True)
            await admin.send_operation(
                "delete_file_metadata",
                {"file_id": old_file["id"]},
            )
    except Exception:
        file_path.unlink(missing_ok=True)
        if metadata_created:
            with suppress(Exception):
                await admin.send_operation(
                    "delete_file_metadata",
                    {"file_id": file_id},
                )
        raise

    return JSONResponse(
        status_code=201,
        headers={"Location": f"/files/{file_id}"},
        content=_uploaded_file_response(
            file_id=file_id,
            file_name=file.filename,
            size_bytes=len(content),
            mime_type=mime_type,
        ),
    )


@files_router.get("")
async def list_files(
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict:
    """List all uploaded files.

    Args:
        _claims: Authenticated user.
        admin: DB Proxy client.

    Returns:
        Dict with files array.
    """
    return await admin.send_operation("list_files")


@files_router.delete("/{file_id}")
async def delete_file(
    file_id: uuid.UUID,
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> Response:
    """Delete a file from disk and database.

    Args:
        file_id: File UUID.
        _claims: Authenticated user.
        admin: DB Proxy client.
    """
    file_id_str = str(file_id)
    file_info = await _require_file_metadata(admin, file_id_str)
    file_path = _file_path(file_id_str, file_info["original_name"])
    file_path.unlink(missing_ok=True)

    await admin.send_operation("delete_file_metadata", {"file_id": file_id_str})
    return Response(status_code=204)


@files_router.get("/{file_id}/content", response_model=None)
async def get_file_content(
    file_id: uuid.UUID,
    request: Request,
    _claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> FileResponse | Response:
    """Read file content.

    Starlette's FileResponse does not evaluate If-None-Match, so the
    conditional check is explicit: the ETag is computed with the same
    stat-based formula FileResponse uses and set on the response
    itself, so a matching If-None-Match yields a 304 with an empty body.

    Args:
        file_id: File UUID.
        request: HTTP request (provides the If-None-Match header).
        _claims: Authenticated user.
        admin: DB Proxy client.

    Returns:
        File content with appropriate Content-Type and
        Cache-Control: private, max-age=86400; 304 when the conditional
        header matches the ETag.

    Raises:
        HTTPException: 404 if the file is missing from the database or disk.
    """
    file_id_str = str(file_id)
    file_info = await _require_file_metadata(admin, file_id_str)
    file_path = _file_path(file_id_str, file_info["original_name"])

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    etag = _file_etag(file_path.stat())
    headers = {"etag": etag, "cache-control": FILE_CONTENT_CACHE_CONTROL}
    if if_none_match_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    return FileResponse(
        path=str(file_path),
        media_type=file_info["mime_type"],
        filename=file_info["original_name"],
        headers=headers,
    )
