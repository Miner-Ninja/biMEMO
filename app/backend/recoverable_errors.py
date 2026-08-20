"""Shared recoverable-error tuples for best-effort callbacks.

A recoverable error is one where a best-effort callback (memory
persistence, embedding, transcription, audit) may be skipped without
failing the turn. Each tuple is the single source of truth for its
callback class; change a tuple only together with the callback's failure
policy.
"""

import httpx

from backend.ipc.ipc_channel import IPCChannelError
from backend.processes.orchestrator.client import DBProxyError


# --- Memory-route and message persistence callbacks ---
RECOVERABLE_MEMORY_CALLBACK_ERRORS = (
    DBProxyError,
    RuntimeError,
    TypeError,
    ValueError,
)

# --- Embedding probe (disables vector search on failure) ---
RECOVERABLE_EMBEDDING_ERRORS = (
    RuntimeError,
    ValueError,
    IPCChannelError,
    httpx.HTTPError,
)

# --- Voice transcription (re-raised as VoiceTranscriptionError) ---
RECOVERABLE_TRANSCRIPTION_ERRORS = (
    RuntimeError,
    ValueError,
    OSError,
    httpx.HTTPError,
)

# --- Transcription audit logging ---
RECOVERABLE_AUDIT_ERRORS = (RuntimeError, IPCChannelError, TypeError, ValueError)
