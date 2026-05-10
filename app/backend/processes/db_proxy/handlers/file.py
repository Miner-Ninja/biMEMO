"""File metadata DB proxy handler factories."""

import json
from typing import TYPE_CHECKING, Any

from ..common import OperationHandler, validate_params
from ..params import (
    CreateFileMetadataParams,
    DeleteFileMetadataParams,
    GetFileContextParams,
    GetFileMetadataParams,
    SaveFileContextParams,
    SaveFileContextUsageParams,
)


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager


def _json_string(value: object) -> str:
    """Serialize one value for JSON storage."""
    return json.dumps(value)


def _json_list_value(value: object) -> list[object]:
    """Decode one optional JSON list cell into a Python list."""
    return json.loads(value) if value else []


def _file_metadata_record(row: tuple[Any, ...]) -> dict[str, Any]:
    """Convert one file metadata row into the API shape."""
    return {
        "id": row[0],
        "original_name": row[1],
        "size": row[2],
        "mime_type": row[3],
        "created_at": row[4],
    }


def _file_context_chunk_record(row: tuple[Any, ...]) -> dict[str, Any]:
    """Convert one stored file context chunk row into the API shape."""
    return {
        "chunk_id": row[0],
        "chunk_index": row[1],
        "text": row[2],
        "line_start": row[3],
        "line_end": row[4],
        "estimated_tokens": row[5],
    }


def _file_context_usage_record(row: tuple[Any, ...] | None) -> dict[str, Any] | None:
    """Convert one file context usage row into the API shape."""
    if row is None:
        return None
    return {
        "processing_mode": row[0],
        "mode_reason": row[1],
        "user_query": row[2],
        "selected_chunk_ids": _json_list_value(row[3]),
        "selected_chunk_indexes": _json_list_value(row[4]),
        "used_at": row[5],
    }


def _file_context_record(
    context_row: tuple[Any, ...],
    chunk_rows: list[tuple[Any, ...]],
    usage_row: tuple[Any, ...] | None,
) -> dict[str, Any]:
    """Convert stored file context rows into the API shape."""
    return {
        "file_id": context_row[0],
        "file_kind": context_row[1],
        "estimated_tokens": context_row[2],
        "processing_mode": context_row[3],
        "processing_reason": context_row[4],
        "summary_overview": context_row[5],
        "summary_structure": _json_list_value(context_row[6]),
        "summary_key_points": _json_list_value(context_row[7]),
        "summary_warnings": _json_list_value(context_row[8]),
        "chunks": [_file_context_chunk_record(row) for row in chunk_rows],
        "last_usage": _file_context_usage_record(usage_row),
    }


def make_create_file_metadata_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for create_file_metadata operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Create a file metadata record."""
        p = validate_params(params, CreateFileMetadataParams)
        db.execute(
            "INSERT INTO files (id, original_name, size, mime_type) "
            "VALUES (?, ?, ?, ?)",
            (p.id, p.original_name, p.size, p.mime_type),
        )
        db.commit()
        return {"id": p.id}

    return handle


def make_list_files_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for list_files operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """List all file metadata."""
        cursor = db.execute(
            "SELECT id, original_name, size, mime_type, created_at "
            "FROM files ORDER BY created_at DESC"
        )
        rows = cursor.fetchall()
        return {"files": [_file_metadata_record(row) for row in rows]}

    return handle


def make_delete_file_metadata_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for delete_file_metadata operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Delete file metadata by ID."""
        p = validate_params(params, DeleteFileMetadataParams)
        cursor = db.execute("DELETE FROM files WHERE id = ?", (p.file_id,))
        db.commit()
        return {"deleted": cursor.rowcount > 0}

    return handle


def make_get_file_metadata_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for get_file_metadata operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Get a single file metadata record by ID."""
        p = validate_params(params, GetFileMetadataParams)
        cursor = db.execute(
            "SELECT id, original_name, size, mime_type, created_at "
            "FROM files WHERE id = ?",
            (p.file_id,),
        )
        row = cursor.fetchone()
        if not row:
            return {"file": None}
        return {"file": _file_metadata_record(row)}

    return handle


def make_save_file_context_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for save_file_context operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Persist one file context summary and its stored chunks."""
        p = validate_params(params, SaveFileContextParams)
        db.execute(
            "INSERT OR REPLACE INTO file_contexts ("
            "file_id, file_kind, estimated_tokens, processing_mode, "
            "processing_reason, "
            "summary_overview, summary_structure, summary_key_points, "
            "summary_warnings, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "strftime('%Y-%m-%dT%H:%M:%f', 'now'))",
            (
                p.file_id,
                p.file_kind,
                p.estimated_tokens,
                p.processing_mode,
                p.processing_reason,
                p.summary_overview,
                _json_string(p.summary_structure),
                _json_string(p.summary_key_points),
                _json_string(p.summary_warnings),
            ),
        )
        db.execute("DELETE FROM file_context_chunks WHERE file_id = ?", (p.file_id,))
        if p.chunks:
            db.executemany(
                "INSERT INTO file_context_chunks ("
                "id, file_id, chunk_index, text, line_start, line_end, "
                "estimated_tokens"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        chunk.chunk_id,
                        p.file_id,
                        chunk.chunk_index,
                        chunk.text,
                        chunk.line_start,
                        chunk.line_end,
                        chunk.estimated_tokens,
                    )
                    for chunk in p.chunks
                ],
            )
        db.commit()
        return {
            "file_id": p.file_id,
            "chunk_count": len(p.chunks),
        }

    return handle


def make_get_file_context_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for get_file_context operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Load one stored file context with ordered chunks."""
        p = validate_params(params, GetFileContextParams)
        context_row = db.execute(
            "SELECT file_id, file_kind, estimated_tokens, processing_mode, "
            "processing_reason, "
            "summary_overview, summary_structure, summary_key_points, "
            "summary_warnings "
            "FROM file_contexts WHERE file_id = ?",
            (p.file_id,),
        ).fetchone()
        if context_row is None:
            return {"file_context": None}

        chunk_rows = db.execute(
            "SELECT id, chunk_index, text, line_start, line_end, estimated_tokens "
            "FROM file_context_chunks WHERE file_id = ? ORDER BY chunk_index ASC",
            (p.file_id,),
        ).fetchall()
        usage_row = db.execute(
            "SELECT processing_mode, mode_reason, user_query, "
            "selected_chunk_ids, selected_chunk_indexes, used_at "
            "FROM file_context_usage WHERE file_id = ?",
            (p.file_id,),
        ).fetchone()
        return {
            "file_context": _file_context_record(
                context_row,
                chunk_rows,
                usage_row,
            )
        }

    return handle


def make_save_file_context_usage_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for save_file_context_usage operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Persist the latest prompt usage snapshot for one stored file context."""
        p = validate_params(params, SaveFileContextUsageParams)
        db.execute(
            "INSERT OR REPLACE INTO file_context_usage ("
            "file_id, processing_mode, mode_reason, user_query, "
            "selected_chunk_ids, selected_chunk_indexes, used_at"
            ") VALUES (?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%f', 'now'))",
            (
                p.file_id,
                p.processing_mode,
                p.mode_reason,
                p.user_query,
                _json_string(p.selected_chunk_ids),
                _json_string(p.selected_chunk_indexes),
            ),
        )
        db.commit()
        return {"file_id": p.file_id}

    return handle
