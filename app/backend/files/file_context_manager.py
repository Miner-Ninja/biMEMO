"""File context manager for attached chat files.

Builds prompt-safe context for uploaded files without mixing file data into
the long-term memory pipeline. Chooses one of four modes: inline, preview,
summarized, or chunked recall based on file size and the active user query.
Also extracts light structural hints so large files can still be discussed
without injecting the full raw content into the model prompt.
"""

from __future__ import annotations

from backend.files.file_analysis import (
    CHUNKED_RECALL_MODE,
    INLINE_MODE,
    PREVIEW_MODE,
    SUMMARIZED_MODE,
    UNKNOWN_KIND,
    build_chunks_block,
    build_inline_block,
    build_metadata_block,
    build_preview_block,
    build_summary_block,
    build_summary_overview,
    build_summary_preview,
    build_summary_warnings,
    chunk_overlap_score,
    detect_file_kind,
    estimate_tokens,
    extract_key_points,
    extract_structure_lines,
    is_exact_detail_query,
    semantic_query_tokens,
)
from backend.files.file_context_models import (
    FileChunk,
    FileContextAnalysis,
    FileContextResult,
    FileSummary,
    FileUsageSnapshot,
    StoredFileContext,
)
from backend.files.file_storage import (
    deserialize_analysis,
    deserialize_chunks,
    deserialize_summary,
    serialize_stored_file_context,
)


# --- Token and content limits ---
INLINE_TOKEN_LIMIT = 1_500
PREVIEW_TOKEN_LIMIT = 4_000
CHUNK_TOKEN_SIZE = 1_200
MAX_PROMPT_CHUNKS = 3


class FileContextManager:
    """Build prompt-safe file context for attached chat files."""

    def __init__(
        self,
        *,
        inline_token_limit: int = INLINE_TOKEN_LIMIT,
        preview_token_limit: int = PREVIEW_TOKEN_LIMIT,
        chunk_token_size: int = CHUNK_TOKEN_SIZE,
        max_prompt_chunks: int = MAX_PROMPT_CHUNKS,
    ) -> None:
        """Store one stable file-context policy."""
        self.inline_token_limit = inline_token_limit
        self.preview_token_limit = preview_token_limit
        self.chunk_token_size = chunk_token_size
        self.max_prompt_chunks = max_prompt_chunks

    def analyze_file(
        self,
        *,
        file_id: str,
        file_name: str,
        mime_type: str,
        content: str,
        user_query: str | None = None,
    ) -> FileContextAnalysis:
        """Classify one attached file and choose a processing mode."""
        estimated = estimate_tokens(content)
        file_kind = detect_file_kind(file_name=file_name, mime_type=mime_type)
        processing_mode = self._resolve_processing_mode(
            estimated_tokens=estimated,
            user_query=user_query,
        )
        return FileContextAnalysis(
            file_id=file_id,
            file_name=file_name,
            mime_type=mime_type,
            size_bytes=len(content.encode("utf-8", errors="replace")),
            estimated_tokens=estimated,
            file_kind=file_kind,
            processing_mode=processing_mode,
        )

    def is_textual_file(
        self,
        *,
        file_name: str,
        mime_type: str,
    ) -> bool:
        """Return True when this file should use the text context pipeline."""
        return detect_file_kind(file_name=file_name, mime_type=mime_type) != (
            UNKNOWN_KIND
        )

    def build_stored_file_context(
        self,
        *,
        file_id: str,
        file_name: str,
        mime_type: str,
        content: str,
    ) -> StoredFileContext:
        """Build the persisted summary and chunk data for one stored file."""
        analysis = self.analyze_file(
            file_id=file_id,
            file_name=file_name,
            mime_type=mime_type,
            content=content,
            user_query=None,
        )
        return self._build_stored_context_from_analysis(
            analysis=analysis,
            content=content,
        )

    def serialize_stored_file_context(
        self,
        *,
        stored_context: StoredFileContext,
    ) -> dict[str, object]:
        """Convert one stored file context to a DB-safe payload."""
        return serialize_stored_file_context(stored_context=stored_context)

    def deserialize_stored_file_context(
        self,
        *,
        file_id: str,
        file_name: str,
        mime_type: str,
        size_bytes: int,
        payload: dict[str, object] | None,
    ) -> StoredFileContext | None:
        """Rebuild one stored file context from DB payload data."""
        if payload is None:
            return None
        return StoredFileContext(
            analysis=deserialize_analysis(
                file_id=file_id,
                file_name=file_name,
                mime_type=mime_type,
                size_bytes=size_bytes,
                payload=payload,
            ),
            processing_reason=str(payload.get("processing_reason", "")),
            summary=deserialize_summary(file_id=file_id, payload=payload),
            chunks=deserialize_chunks(file_id=file_id, payload=payload),
        )

    def build_file_context_from_storage(
        self,
        *,
        stored_context: StoredFileContext,
        user_query: str | None = None,
        content: str | None = None,
    ) -> FileContextResult:
        """Build the prompt context for one file from persisted summary data."""
        processing_mode = self._resolve_processing_mode(
            estimated_tokens=stored_context.analysis.estimated_tokens,
            user_query=user_query,
        )
        analysis = _with_processing_mode(
            stored_context.analysis,
            processing_mode=processing_mode,
        )
        return self._build_context_result(
            analysis=analysis,
            user_query=user_query,
            content=content,
            summary=stored_context.summary,
            chunks=stored_context.chunks,
        )

    def explain_processing_mode(
        self,
        *,
        estimated_tokens: int,
        user_query: str | None,
    ) -> str:
        """Explain why one file processing mode was chosen."""
        if estimated_tokens <= self.inline_token_limit:
            return (
                f"Estimated size is about {estimated_tokens} tokens, which stays "
                f"within the inline limit of {self.inline_token_limit}."
            )
        if user_query and is_exact_detail_query(user_query):
            return (
                f"Estimated size is about {estimated_tokens} tokens, which is above "
                f"the inline limit of {self.inline_token_limit}, and the query "
                "asks for exact details, so chunked recall was used."
            )
        if estimated_tokens <= self.preview_token_limit:
            return (
                f"Estimated size is about {estimated_tokens} tokens, which is above "
                f"the inline limit of {self.inline_token_limit} but still within "
                f"the preview limit of {self.preview_token_limit}."
            )
        return (
            f"Estimated size is about {estimated_tokens} tokens, which exceeds the "
            f"preview limit of {self.preview_token_limit}, so the file was "
            "summarized before prompt injection."
        )

    def build_usage_snapshot(
        self,
        *,
        file_context: FileContextResult,
        user_query: str,
    ) -> FileUsageSnapshot:
        """Build one persisted usage snapshot from a prompt-ready file context."""
        chunks = file_context.chunks or []
        return FileUsageSnapshot(
            processing_mode=file_context.analysis.processing_mode,
            mode_reason=self.explain_processing_mode(
                estimated_tokens=file_context.analysis.estimated_tokens,
                user_query=user_query,
            ),
            user_query=user_query,
            selected_chunk_ids=[chunk.chunk_id for chunk in chunks],
            selected_chunk_indexes=[chunk.chunk_index for chunk in chunks],
        )

    def build_file_context(
        self,
        *,
        file_id: str,
        file_name: str,
        mime_type: str,
        content: str,
        user_query: str | None = None,
    ) -> FileContextResult:
        """Build the full prompt context for one attached text file."""
        analysis = self.analyze_file(
            file_id=file_id,
            file_name=file_name,
            mime_type=mime_type,
            content=content,
            user_query=user_query,
        )
        return self._build_context_result(
            analysis=analysis,
            user_query=user_query,
            content=content,
            summary=None,
            chunks=None,
        )

    def summarize_file(
        self,
        *,
        analysis: FileContextAnalysis,
        content: str,
    ) -> FileSummary:
        """Create a deterministic structural summary for one larger file."""
        preview = build_summary_preview(content)
        structure = extract_structure_lines(
            file_kind=analysis.file_kind,
            content=preview,
        )
        key_points_list = extract_key_points(
            file_kind=analysis.file_kind,
            content=preview,
        )
        overview = build_summary_overview(
            analysis=analysis,
            content=preview,
            structure=structure,
            key_points=key_points_list,
        )
        return FileSummary(
            file_id=analysis.file_id,
            overview=overview,
            structure=structure,
            key_points=key_points_list,
            warnings=build_summary_warnings(analysis),
        )

    def chunk_file(
        self,
        *,
        file_id: str,
        content: str,
    ) -> list[FileChunk]:
        """Split a larger file into bounded line-aware chunks."""
        lines = _chunk_source_lines(content)
        chunks: list[FileChunk] = []
        current_lines: list[str] = []
        current_token_count = 0
        current_start = 1
        chunk_index = 0

        for line_number, line in enumerate(lines, start=1):
            line_token_count = estimate_tokens(line)
            if _should_flush_chunk(
                current_lines=current_lines,
                current_token_count=current_token_count,
                next_line_token_count=line_token_count,
                chunk_token_size=self.chunk_token_size,
            ):
                chunks.append(
                    _build_chunk(
                        file_id=file_id,
                        chunk_index=chunk_index,
                        lines=current_lines,
                        line_start=current_start,
                        line_end=line_number - 1,
                    )
                )
                chunk_index += 1
                current_lines = []
                current_token_count = 0
                current_start = line_number

            current_lines.append(line)
            current_token_count += line_token_count

        if current_lines:
            chunks.append(
                _build_chunk(
                    file_id=file_id,
                    chunk_index=chunk_index,
                    lines=current_lines,
                    line_start=current_start,
                    line_end=len(lines),
                )
            )
        return chunks

    def select_relevant_chunks(
        self,
        *,
        chunks: list[FileChunk],
        user_query: str,
    ) -> list[FileChunk]:
        """Pick the strongest chunks for one query against a large file."""
        query_tokens = semantic_query_tokens(user_query)
        if not query_tokens:
            return chunks[: self.max_prompt_chunks]

        ranked_chunks = sorted(
            chunks,
            key=lambda chunk: (
                chunk_overlap_score(chunk.text, query_tokens),
                -chunk.chunk_index,
            ),
            reverse=True,
        )
        selected = ranked_chunks[: self.max_prompt_chunks]
        return sorted(selected, key=lambda chunk: chunk.chunk_index)

    def build_prompt_blocks(
        self,
        *,
        analysis: FileContextAnalysis,
        summary: FileSummary | None,
        chunks: list[FileChunk] | None,
        content: str,
    ) -> list[str]:
        """Assemble the final prompt blocks for one non-inline file."""
        blocks = [build_metadata_block(analysis)]
        if summary is not None:
            blocks.append(build_summary_block(summary))
        if chunks:
            blocks.append(build_chunks_block(chunks))
        elif analysis.processing_mode == PREVIEW_MODE:
            blocks.append(build_preview_block(analysis, content))
        return blocks

    def _build_stored_context_from_analysis(
        self,
        *,
        analysis: FileContextAnalysis,
        content: str,
    ) -> StoredFileContext:
        """Build stored file data from one existing analysis result."""
        if analysis.processing_mode in {INLINE_MODE, PREVIEW_MODE}:
            return StoredFileContext(
                analysis=analysis,
                processing_reason=self._processing_reason(
                    estimated_tokens=analysis.estimated_tokens,
                    user_query=None,
                ),
            )
        return StoredFileContext(
            analysis=analysis,
            processing_reason=self._processing_reason(
                estimated_tokens=analysis.estimated_tokens,
                user_query=None,
            ),
            summary=self.summarize_file(analysis=analysis, content=content),
            chunks=self.chunk_file(file_id=analysis.file_id, content=content),
        )

    def _build_context_result(
        self,
        *,
        analysis: FileContextAnalysis,
        user_query: str | None,
        content: str | None,
        summary: FileSummary | None,
        chunks: list[FileChunk] | None,
    ) -> FileContextResult:
        """Build prompt-ready context from raw or stored file data."""
        if analysis.processing_mode == INLINE_MODE:
            inline_content = _require_content(
                content=content,
                error_message="Inline file context requires raw content",
            )
            return FileContextResult(
                analysis=analysis,
                prompt_blocks=[build_inline_block(analysis.file_name, inline_content)],
            )
        if analysis.processing_mode == PREVIEW_MODE:
            preview_content = _require_content(
                content=content,
                error_message="Preview file context requires raw content",
            )
            return FileContextResult(
                analysis=analysis,
                prompt_blocks=[build_preview_block(analysis, preview_content)],
            )

        resolved_summary = self._resolve_context_summary(
            analysis=analysis,
            summary=summary,
            content=content,
        )
        if analysis.processing_mode == SUMMARIZED_MODE:
            return self._build_summarized_context_result(
                analysis=analysis,
                summary=resolved_summary,
                content=content or "",
            )
        return self._build_chunked_context_result(
            analysis=analysis,
            summary=resolved_summary,
            chunks=chunks,
            user_query=user_query or "",
            content=content,
        )

    def _build_summarized_context_result(
        self,
        *,
        analysis: FileContextAnalysis,
        summary: FileSummary,
        content: str,
    ) -> FileContextResult:
        """Build one summary-mode file context result."""
        return FileContextResult(
            analysis=analysis,
            prompt_blocks=self.build_prompt_blocks(
                analysis=analysis,
                summary=summary,
                chunks=None,
                content=content,
            ),
            summary=summary,
        )

    def _build_chunked_context_result(
        self,
        *,
        analysis: FileContextAnalysis,
        summary: FileSummary,
        chunks: list[FileChunk] | None,
        user_query: str,
        content: str | None,
    ) -> FileContextResult:
        """Build one chunked-recall file context result."""
        resolved_chunks = self._resolve_context_chunks(
            analysis=analysis,
            chunks=chunks,
            content=content,
        )
        selected_chunks = self.select_relevant_chunks(
            chunks=resolved_chunks,
            user_query=user_query,
        )
        return FileContextResult(
            analysis=analysis,
            prompt_blocks=self.build_prompt_blocks(
                analysis=analysis,
                summary=summary,
                chunks=selected_chunks,
                content=content or "",
            ),
            summary=summary,
            chunks=selected_chunks,
        )

    def _resolve_context_summary(
        self,
        *,
        analysis: FileContextAnalysis,
        summary: FileSummary | None,
        content: str | None,
    ) -> FileSummary:
        """Return a summary, rebuilding it only when needed."""
        if summary is not None:
            return summary
        raw_content = _require_content(
            content=content,
            error_message="Stored large file context requires a saved summary",
        )
        return self.summarize_file(analysis=analysis, content=raw_content)

    def _resolve_context_chunks(
        self,
        *,
        analysis: FileContextAnalysis,
        chunks: list[FileChunk] | None,
        content: str | None,
    ) -> list[FileChunk]:
        """Return chunks, rebuilding them only when needed."""
        if chunks:
            return chunks
        raw_content = _require_content(
            content=content,
            error_message="Chunked recall requires stored chunks or raw content",
        )
        return self.chunk_file(file_id=analysis.file_id, content=raw_content)

    def _processing_reason(
        self,
        *,
        estimated_tokens: int,
        user_query: str | None,
    ) -> str:
        """Build one readable processing reason from stable inputs."""
        return self.explain_processing_mode(
            estimated_tokens=estimated_tokens,
            user_query=user_query,
        )

    def _resolve_processing_mode(
        self,
        *,
        estimated_tokens: int,
        user_query: str | None,
    ) -> str:
        """Choose the file processing mode for one content size and query."""
        if estimated_tokens <= self.inline_token_limit:
            return INLINE_MODE
        if user_query and is_exact_detail_query(user_query):
            return CHUNKED_RECALL_MODE
        if estimated_tokens <= self.preview_token_limit:
            return PREVIEW_MODE
        return SUMMARIZED_MODE


def _with_processing_mode(
    analysis: FileContextAnalysis,
    *,
    processing_mode: str,
) -> FileContextAnalysis:
    """Clone one file analysis with a different active processing mode."""
    return FileContextAnalysis(
        file_id=analysis.file_id,
        file_name=analysis.file_name,
        mime_type=analysis.mime_type,
        size_bytes=analysis.size_bytes,
        estimated_tokens=analysis.estimated_tokens,
        file_kind=analysis.file_kind,
        processing_mode=processing_mode,
    )


def _require_content(
    *,
    content: str | None,
    error_message: str,
) -> str:
    """Return raw file content or raise a mode-specific error."""
    if content is None:
        raise ValueError(error_message)
    return content


def _chunk_source_lines(content: str) -> list[str]:
    """Return the line source used for chunk splitting."""
    return content.splitlines() or [content]


def _should_flush_chunk(
    *,
    current_lines: list[str],
    current_token_count: int,
    next_line_token_count: int,
    chunk_token_size: int,
) -> bool:
    """Return True when adding the next line would overflow the chunk."""
    return bool(
        current_lines and current_token_count + next_line_token_count > chunk_token_size
    )


def _build_chunk(
    *,
    file_id: str,
    chunk_index: int,
    lines: list[str],
    line_start: int,
    line_end: int,
) -> FileChunk:
    """Build one immutable file chunk object."""
    text = "\n".join(lines).strip()
    return FileChunk(
        file_id=file_id,
        chunk_id=f"{file_id}:{chunk_index}",
        chunk_index=chunk_index,
        text=text,
        line_start=line_start,
        line_end=line_end,
        estimated_tokens=estimate_tokens(text),
    )
