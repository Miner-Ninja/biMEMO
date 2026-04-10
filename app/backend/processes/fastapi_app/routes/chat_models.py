"""Dataclass models used by chat route handlers and helpers.

Defines frozen dataclasses for prepared chat inputs, file descriptors,
and intermediate file processing results. These models are passed between
chat helpers and route handlers to carry resolved file metadata, prompt
blocks, and inline image data through the request pipeline.
"""

from dataclasses import dataclass
from pathlib import Path

from backend.ipc.models import ChatRequest as IPCChatRequest


@dataclass(frozen=True, slots=True)
class ChatInputContext:
    """Prepared chat input with resolved attachments and prompt text."""

    message: str
    images: list[str]
    resolved_files: list[str]


@dataclass(frozen=True, slots=True)
class PreparedChatRequest:
    """Everything needed to send one web chat request to the orchestrator."""

    conversation_id: str
    ipc_request: IPCChatRequest


@dataclass(frozen=True, slots=True)
class StoredChatFile:
    """Stored file metadata prepared for chat input handling."""

    file_id: str
    original_name: str
    mime_type: str
    size_bytes: int
    file_path: Path


@dataclass(frozen=True, slots=True)
class PreparedFileInput:
    """One file converted into prompt blocks, inline images, and file labels."""

    prompt_blocks: list[str]
    images: list[str]
    resolved_files: list[str]


@dataclass(frozen=True, slots=True)
class PreparedFileCollection:
    """Accumulated prompt parts and file labels for all attached files."""

    prompt_blocks: list[str]
    images: list[str]
    resolved_files: list[str]
