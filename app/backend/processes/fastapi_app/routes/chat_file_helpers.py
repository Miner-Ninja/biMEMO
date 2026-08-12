"""File handling helpers for chat route handlers.

Provides functions for reading files, loading file metadata, resolving
stored file descriptors, validating request images, and preparing file
context for prompt injection. Works with the FileContextManager and
AdminDBClient to build prompt blocks and inline images from attached files.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException

from backend.files import FileContextManager
from backend.ipc.ipc_channel import IPCChannelError
from backend.security.content_filter import (
    ImageValidationError,
    validate_image_data_url,
)

from . import files as files_routes
from .chat_models import (
    ChatInputContext,
    PreparedFileCollection,
    PreparedFileInput,
    StoredChatFile,
)


if TYPE_CHECKING:
    from backend.models.requests import ChatRequest as HTTPChatRequest

    from ..clients import AdminDBClient


logger = logging.getLogger(__name__)

IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
METADATA_ONLY_MODE = "metadata_only"
NON_TEXT_FILE_WARNING = (
    "This file is attached, but text extraction is not available for its type yet."
)
MISSING_RAW_FILE_WARNING = (
    "This file is attached and stored file context exists, but the raw file bytes "
    "are no longer available on disk, so only file metadata could be passed "
    "to the model."
)
MAX_INLINE_FILE_IMAGES = 4
MAX_FILE_IMAGE_SIZE_BYTES = 10 * 1024 * 1024
FILE_CONTEXT_MANAGER = FileContextManager()

RECOVERABLE_FILE_CONTEXT_ERRORS = (OSError, RuntimeError, IPCChannelError)


def build_metadata_only_file_block(
    *,
    file_name: str,
    mime_type: str,
    size_bytes: int,
    warning: str = NON_TEXT_FILE_WARNING,
) -> str:
    """Build a safe prompt block for a non-text file."""
    return (
        f"Attached file: {file_name}\n"
        f"Mode: {METADATA_ONLY_MODE}\n"
        f"MIME type: {mime_type}\n"
        f"Size: {size_bytes} bytes\n"
        f"{warning}"
    )


def validate_request_images(images: list[str]) -> None:
    """Validate inline image payloads from the chat request."""
    for image_url in images:
        try:
            validate_image_data_url(image_url)
        except ImageValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc


async def read_path_bytes(file_path: Path) -> bytes:
    """Read one file as bytes without blocking the event loop."""
    return await asyncio.to_thread(file_path.read_bytes)


async def read_path_text(file_path: Path) -> str:
    """Read one text file without blocking the event loop."""
    return await asyncio.to_thread(file_path.read_text, errors="replace")


async def path_exists(file_path: Path) -> bool:
    """Check file existence without blocking the event loop."""
    return await asyncio.to_thread(file_path.exists)


async def load_file_metadata_map(
    admin_client: AdminDBClient,
) -> dict[str, dict[str, object]]:
    """Load all stored file metadata keyed by file identifier."""
    try:
        files_response = await admin_client.send_operation("list_files")
    except RECOVERABLE_FILE_CONTEXT_ERRORS:
        logger.warning(
            "Failed to list files for context injection",
            exc_info=True,
        )
        return {}
    all_files = files_response.get("files", [])
    return {
        str(file_data["id"]): file_data
        for file_data in all_files
        if isinstance(file_data, dict) and "id" in file_data
    }


def resolve_stored_chat_file(
    *,
    file_id: str,
    file_metadata: dict[str, object],
) -> StoredChatFile:
    """Build one stored-file descriptor for chat input preparation."""
    original_name = str(file_metadata["original_name"])
    extension = Path(original_name).suffix.lower()
    return StoredChatFile(
        file_id=file_id,
        original_name=original_name,
        mime_type=str(file_metadata.get("mime_type", "") or ""),
        size_bytes=int(file_metadata.get("size", 0)),
        file_path=files_routes.FILES_DIR / f"{file_id}{extension}",
    )


async def append_inline_image(
    *,
    file_path: Path,
    mime_type: str,
    original_name: str,
    images: list[str],
    resolved_files: list[str],
) -> None:
    """Append one stored image file as an inline data URL."""
    if not await path_exists(file_path):
        return
    file_bytes = await read_path_bytes(file_path)
    base64_data = base64.b64encode(file_bytes).decode()
    images.append(f"data:{mime_type};base64,{base64_data}")
    resolved_files.append(original_name)


async def load_stored_file_context_payload(
    *,
    admin_client: AdminDBClient,
    file_id: str,
) -> dict[str, object] | None:
    """Load one stored file-context payload, if it exists."""
    try:
        context_response = await admin_client.send_operation(
            "get_file_context",
            {"file_id": file_id},
        )
    except RECOVERABLE_FILE_CONTEXT_ERRORS:
        logger.warning(
            "Failed to load stored file context for %s",
            file_id,
            exc_info=True,
        )
        return None
    payload = context_response.get("file_context")
    if isinstance(payload, dict):
        return payload
    return None


async def build_file_context_from_stored_state(
    *,
    body: HTTPChatRequest,
    file_id: str,
    original_name: str,
    mime_type: str,
    size_bytes: int,
    file_path: Path,
    stored_context_payload: dict[str, object] | None,
) -> tuple[object, str] | None:
    """Build one file context from stored state when saved context exists."""
    stored_context = FILE_CONTEXT_MANAGER.deserialize_stored_file_context(
        file_id=file_id,
        file_name=original_name,
        mime_type=mime_type,
        size_bytes=size_bytes,
        payload=stored_context_payload,
    )
    if stored_context is None:
        return None

    stored_content: str | None = None
    needs_raw_content = (
        stored_context.analysis.estimated_tokens
        <= FILE_CONTEXT_MANAGER.preview_token_limit
    )
    if needs_raw_content:
        if not await path_exists(file_path):
            logger.warning(
                "Missing raw file for stored small context %s",
                original_name,
            )
            metadata_only_block = build_metadata_only_file_block(
                file_name=original_name,
                mime_type=mime_type,
                size_bytes=size_bytes,
                warning=MISSING_RAW_FILE_WARNING,
            )
            return (
                PreparedFileInput(
                    prompt_blocks=[metadata_only_block],
                    images=[],
                    resolved_files=[
                        f"{original_name} [{METADATA_ONLY_MODE}]",
                    ],
                ),
                METADATA_ONLY_MODE,
            )
        stored_content = await read_path_text(file_path)

    file_context = FILE_CONTEXT_MANAGER.build_file_context_from_storage(
        stored_context=stored_context,
        user_query=body.message,
        content=stored_content,
    )
    return file_context, file_context.analysis.processing_mode


async def build_file_context_from_raw_text(
    *,
    body: HTTPChatRequest,
    file_id: str,
    original_name: str,
    mime_type: str,
    file_path: Path,
) -> object | None:
    """Build one file context directly from raw text on disk."""
    if not await path_exists(file_path):
        return None
    text = await read_path_text(file_path)
    return FILE_CONTEXT_MANAGER.build_file_context(
        file_id=file_id,
        file_name=original_name,
        mime_type=mime_type,
        content=text,
        user_query=body.message,
    )


async def save_file_context_usage_snapshot(
    *,
    admin_client: AdminDBClient,
    body: HTTPChatRequest,
    file_id: str,
    file_context: object,
) -> None:
    """Persist the usage snapshot for one prepared file context."""
    usage_snapshot = FILE_CONTEXT_MANAGER.build_usage_snapshot(
        file_context=file_context,
        user_query=body.message,
    )
    await admin_client.send_operation(
        "save_file_context_usage",
        {
            "file_id": file_id,
            "processing_mode": usage_snapshot.processing_mode,
            "mode_reason": usage_snapshot.mode_reason,
            "user_query": usage_snapshot.user_query,
            "selected_chunk_ids": usage_snapshot.selected_chunk_ids,
            "selected_chunk_indexes": usage_snapshot.selected_chunk_indexes,
        },
    )


async def build_text_file_prompt_blocks(
    *,
    body: HTTPChatRequest,
    admin_client: AdminDBClient,
    file_id: str,
    original_name: str,
    mime_type: str,
    size_bytes: int,
    file_path: Path,
) -> tuple[list[str], str]:
    """Build prompt blocks and display label for one textual file."""
    file_context, processing_mode = await resolve_text_file_context(
        body=body,
        admin_client=admin_client,
        file_id=file_id,
        original_name=original_name,
        mime_type=mime_type,
        size_bytes=size_bytes,
        file_path=file_path,
    )
    if file_context is None:
        return [], ""
    if isinstance(file_context, PreparedFileInput):
        return file_context.prompt_blocks, file_context.resolved_files[0]

    await save_file_context_usage_snapshot(
        admin_client=admin_client,
        body=body,
        file_id=file_id,
        file_context=file_context,
    )
    return file_context.prompt_blocks, (f"{original_name} [{processing_mode}]")


async def resolve_text_file_context(
    *,
    body: HTTPChatRequest,
    admin_client: AdminDBClient,
    file_id: str,
    original_name: str,
    mime_type: str,
    size_bytes: int,
    file_path: Path,
) -> tuple[object | PreparedFileInput | None, str]:
    """Resolve one text file into stored context, raw context, or metadata-only."""
    stored_context_payload = await load_stored_file_context_payload(
        admin_client=admin_client,
        file_id=file_id,
    )
    stored_context_result = await build_file_context_from_stored_state(
        body=body,
        file_id=file_id,
        original_name=original_name,
        mime_type=mime_type,
        size_bytes=size_bytes,
        file_path=file_path,
        stored_context_payload=stored_context_payload,
    )
    if stored_context_result is not None:
        return stored_context_result

    raw_file_context = await build_file_context_from_raw_text(
        body=body,
        file_id=file_id,
        original_name=original_name,
        mime_type=mime_type,
        file_path=file_path,
    )
    if raw_file_context is None:
        return None, ""
    return raw_file_context, raw_file_context.analysis.processing_mode


async def prepare_single_file_input(
    *,
    body: HTTPChatRequest,
    admin_client: AdminDBClient,
    stored_file: StoredChatFile,
    current_image_count: int,
) -> PreparedFileInput:
    """Prepare one stored file for prompt injection and inline image forwarding."""
    if stored_file.mime_type in IMAGE_MIME_TYPES:
        return await prepare_image_file_input(
            stored_file=stored_file,
            current_image_count=current_image_count,
        )

    if not FILE_CONTEXT_MANAGER.is_textual_file(
        file_name=stored_file.original_name,
        mime_type=stored_file.mime_type,
    ):
        return build_metadata_only_file_input(
            stored_file=stored_file,
        )

    prompt_blocks, resolved_file_label = await build_text_file_prompt_blocks(
        body=body,
        admin_client=admin_client,
        file_id=stored_file.file_id,
        original_name=stored_file.original_name,
        mime_type=stored_file.mime_type,
        size_bytes=stored_file.size_bytes,
        file_path=stored_file.file_path,
    )
    return PreparedFileInput(
        prompt_blocks=prompt_blocks,
        images=[],
        resolved_files=[resolved_file_label] if resolved_file_label else [],
    )


async def prepare_image_file_input(
    *,
    stored_file: StoredChatFile,
    current_image_count: int,
) -> PreparedFileInput:
    """Prepare one stored image file as inline image input."""
    if current_image_count >= MAX_INLINE_FILE_IMAGES:
        return PreparedFileInput(prompt_blocks=[], images=[], resolved_files=[])
    if stored_file.size_bytes > MAX_FILE_IMAGE_SIZE_BYTES:
        return PreparedFileInput(prompt_blocks=[], images=[], resolved_files=[])

    inline_images: list[str] = []
    resolved_files: list[str] = []
    await append_inline_image(
        file_path=stored_file.file_path,
        mime_type=stored_file.mime_type,
        original_name=stored_file.original_name,
        images=inline_images,
        resolved_files=resolved_files,
    )
    return PreparedFileInput(
        prompt_blocks=[],
        images=inline_images,
        resolved_files=resolved_files,
    )


def build_metadata_only_file_input(
    *,
    stored_file: StoredChatFile,
) -> PreparedFileInput:
    """Prepare one non-text file as a metadata-only prompt block."""
    return PreparedFileInput(
        prompt_blocks=[
            build_metadata_only_file_block(
                file_name=stored_file.original_name,
                mime_type=stored_file.mime_type,
                size_bytes=stored_file.size_bytes,
            )
        ],
        images=[],
        resolved_files=[
            f"{stored_file.original_name} [{METADATA_ONLY_MODE}]",
        ],
    )


async def collect_file_input_parts(
    *,
    body: HTTPChatRequest,
    admin_client: AdminDBClient,
    initial_images: list[str],
) -> PreparedFileCollection:
    """Collect prompt blocks and resolved file labels for all attached files."""
    file_context_parts: list[str] = []
    images = list(initial_images)
    resolved_files: list[str] = []
    id_to_file = await load_file_metadata_map(admin_client)
    for file_id in body.file_ids:
        stored_file = resolve_stored_chat_file_from_collection(
            file_id=file_id,
            id_to_file=id_to_file,
        )
        if stored_file is None:
            continue

        try:
            prepared_file = await prepare_single_file_input(
                body=body,
                admin_client=admin_client,
                stored_file=stored_file,
                current_image_count=len(images),
            )
        except RECOVERABLE_FILE_CONTEXT_ERRORS:
            logger.warning(
                "Failed to read file %s",
                stored_file.original_name,
                exc_info=True,
            )
            continue
        file_context_parts.extend(prepared_file.prompt_blocks)
        images.extend(prepared_file.images)
        resolved_files.extend(prepared_file.resolved_files)
    return PreparedFileCollection(
        prompt_blocks=file_context_parts,
        images=images,
        resolved_files=resolved_files,
    )


def resolve_stored_chat_file_from_collection(
    *,
    file_id: str,
    id_to_file: dict[str, dict[str, object]],
) -> StoredChatFile | None:
    """Resolve one attached file id into stored file metadata."""
    file_metadata = id_to_file.get(file_id)
    if not file_metadata:
        return None
    return resolve_stored_chat_file(
        file_id=file_id,
        file_metadata=file_metadata,
    )


async def prepare_chat_input_context(
    *,
    body: HTTPChatRequest,
    admin_client: AdminDBClient,
) -> ChatInputContext:
    """Prepare final prompt text, inline images, and resolved file labels."""
    validate_request_images(body.images)
    message = body.message
    images = list(body.images)
    resolved_files: list[str] = []
    if not body.file_ids:
        return ChatInputContext(
            message=message,
            images=images,
            resolved_files=resolved_files,
            forced_tools=body.forced_tools,
        )

    prepared_files = await collect_file_input_parts(
        body=body,
        admin_client=admin_client,
        initial_images=images,
    )

    if prepared_files.prompt_blocks:
        message = f"{'\n\n'.join(prepared_files.prompt_blocks)}\n\n{message}"
    return ChatInputContext(
        message=message,
        images=prepared_files.images,
        resolved_files=prepared_files.resolved_files,
        forced_tools=body.forced_tools,
    )
