"""Data models for file context processing.

Defines the dataclasses used by the file context manager to represent
file analysis results, summaries, chunks, and prompt-ready context
blocks. These models carry structured metadata through the file
context pipeline without any processing logic.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FileContextAnalysis:
    """Static analysis result for one attached file."""

    file_id: str
    file_name: str
    mime_type: str
    size_bytes: int
    estimated_tokens: int
    file_kind: str
    processing_mode: str


@dataclass(frozen=True, slots=True)
class FileSummary:
    """Structured summary for a non-inline file."""

    file_id: str
    overview: str
    structure: list[str]
    key_points: list[str]
    warnings: list[str]


@dataclass(frozen=True, slots=True)
class FileChunk:
    """A bounded chunk from one larger text file."""

    file_id: str
    chunk_id: str
    chunk_index: int
    text: str
    line_start: int | None
    line_end: int | None
    estimated_tokens: int


@dataclass(frozen=True, slots=True)
class FileContextResult:
    """Prompt-ready context blocks for one attached file."""

    analysis: FileContextAnalysis
    prompt_blocks: list[str]
    summary: FileSummary | None = None
    chunks: list[FileChunk] | None = None


@dataclass(frozen=True, slots=True)
class FileUsageSnapshot:
    """Latest prompt-level usage details for one attached file."""

    processing_mode: str
    mode_reason: str
    user_query: str
    selected_chunk_ids: list[str]
    selected_chunk_indexes: list[int]


@dataclass(frozen=True, slots=True)
class StoredFileContext:
    """Persisted file context data detached from the raw file bytes."""

    analysis: FileContextAnalysis
    processing_reason: str = ""
    summary: FileSummary | None = None
    chunks: list[FileChunk] | None = None
