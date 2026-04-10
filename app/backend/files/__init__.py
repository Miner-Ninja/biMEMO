"""File context processing for chat-attached documents.

Provides a dedicated file context pipeline separate from user memory.
Builds inline, preview, summarized, or chunk-selected prompt blocks
depending on file size and the active user query. Used by chat routes
to keep large files from overwhelming prompt assembly.
"""

from backend.files.file_context_manager import FileContextManager
from backend.files.file_context_models import (
    FileChunk,
    FileContextAnalysis,
    FileContextResult,
    FileSummary,
    FileUsageSnapshot,
    StoredFileContext,
)


__all__ = [
    "FileChunk",
    "FileContextAnalysis",
    "FileContextManager",
    "FileContextResult",
    "FileSummary",
    "FileUsageSnapshot",
    "StoredFileContext",
]
