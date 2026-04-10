"""Serialization and deserialization for persisted file context data.

Converts file context models to and from DB-safe dictionary payloads.
All functions are stateless — they receive model instances or raw
payload dictionaries and return transformed data without any database
or I/O interaction.
"""

from __future__ import annotations

from backend.files.file_context_models import (
    FileChunk,
    FileContextAnalysis,
    FileSummary,
    StoredFileContext,
)


def serialize_stored_file_context(
    *,
    stored_context: StoredFileContext,
) -> dict[str, object]:
    """Convert one stored file context to a DB-safe payload."""
    return {
        "file_id": stored_context.analysis.file_id,
        "file_kind": stored_context.analysis.file_kind,
        "estimated_tokens": stored_context.analysis.estimated_tokens,
        "processing_mode": stored_context.analysis.processing_mode,
        "processing_reason": stored_context.processing_reason,
        **serialize_summary(stored_context.summary),
        "chunks": serialize_chunks(stored_context.chunks),
    }


def serialize_summary(summary: FileSummary | None) -> dict[str, object]:
    """Serialize one optional file summary into DB fields."""
    if summary is None:
        return {
            "summary_overview": "",
            "summary_structure": [],
            "summary_key_points": [],
            "summary_warnings": [],
        }
    return {
        "summary_overview": summary.overview,
        "summary_structure": summary.structure,
        "summary_key_points": summary.key_points,
        "summary_warnings": summary.warnings,
    }


def serialize_chunks(
    chunks: list[FileChunk] | None,
) -> list[dict[str, object]]:
    """Serialize stored chunks into DB-safe dictionaries."""
    return [
        {
            "chunk_id": chunk.chunk_id,
            "chunk_index": chunk.chunk_index,
            "text": chunk.text,
            "line_start": chunk.line_start,
            "line_end": chunk.line_end,
            "estimated_tokens": chunk.estimated_tokens,
        }
        for chunk in (chunks or [])
    ]


def deserialize_analysis(
    *,
    file_id: str,
    file_name: str,
    mime_type: str,
    size_bytes: int,
    payload: dict[str, object],
) -> FileContextAnalysis:
    """Rebuild stored analysis metadata from persisted payload data."""
    return FileContextAnalysis(
        file_id=file_id,
        file_name=file_name,
        mime_type=mime_type,
        size_bytes=size_bytes,
        estimated_tokens=int(payload["estimated_tokens"]),
        file_kind=str(payload["file_kind"]),
        processing_mode=str(payload["processing_mode"]),
    )


def deserialize_summary(
    *,
    file_id: str,
    payload: dict[str, object],
) -> FileSummary | None:
    """Rebuild a stored file summary from persisted payload data."""
    overview = str(payload.get("summary_overview", ""))
    structure = payload_string_list(payload, "summary_structure")
    key_points = payload_string_list(payload, "summary_key_points")
    warnings = payload_string_list(payload, "summary_warnings")
    if not overview and not structure and not key_points and not warnings:
        return None
    return FileSummary(
        file_id=file_id,
        overview=overview,
        structure=structure,
        key_points=key_points,
        warnings=warnings,
    )


def deserialize_chunks(
    *,
    file_id: str,
    payload: dict[str, object],
) -> list[FileChunk]:
    """Rebuild stored file chunks from one persisted payload."""
    raw_chunks = payload.get("chunks", [])
    if not isinstance(raw_chunks, list):
        return []
    chunks: list[FileChunk] = []
    for raw_chunk in raw_chunks:
        if not isinstance(raw_chunk, dict):
            continue
        chunks.append(deserialize_chunk(file_id=file_id, payload=raw_chunk))
    return chunks


def deserialize_chunk(
    *,
    file_id: str,
    payload: dict[str, object],
) -> FileChunk:
    """Rebuild one stored file chunk from persisted payload data."""
    return FileChunk(
        file_id=file_id,
        chunk_id=str(payload["chunk_id"]),
        chunk_index=int(payload["chunk_index"]),
        text=str(payload["text"]),
        line_start=payload_optional_int(payload, "line_start"),
        line_end=payload_optional_int(payload, "line_end"),
        estimated_tokens=int(payload["estimated_tokens"]),
    )


def payload_string_list(
    payload: dict[str, object],
    key: str,
) -> list[str]:
    """Return one payload field as a list of strings."""
    raw_items = payload.get(key, [])
    if not isinstance(raw_items, list):
        return []
    return [str(item) for item in raw_items]


def payload_optional_int(
    payload: dict[str, object],
    key: str,
) -> int | None:
    """Return one payload field as an optional integer."""
    value = payload.get(key)
    if value is None:
        return None
    return int(value)
