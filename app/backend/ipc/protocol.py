"""IPC protocol components for worker isolation.

Provides HMAC-SHA256 authenticated communication over Unix domain sockets.
Generic enough to work with any tool worker implementation.
"""

import hashlib
import hmac
import logging
import struct
from typing import Any

from pydantic import BaseModel, ConfigDict


logger = logging.getLogger(__name__)

# --- Protocol constants ---
HMAC_TAG_LEN = 32
LENGTH_PREFIX_LEN = 4
MAX_MESSAGE_SIZE = 2 * 1024 * 1024  # 2 MB


class AuthenticationError(Exception):
    """Raised when HMAC verification fails."""


class IPCMessageModel(BaseModel):
    """Base model for strict IPC message payloads."""

    model_config = ConfigDict(extra="forbid")


class ToolRequest(IPCMessageModel):
    """Request from Core to Tool Worker.

    Attributes:
        request_id: Unique identifier for this request.
        tool_name: Name of the tool to execute.
        parameters: Tool-specific parameters.
    """

    request_id: str
    tool_name: str
    parameters: dict[str, Any]


class ToolResponse(IPCMessageModel):
    """Response from Tool Worker to Core.

    Attributes:
        request_id: Matching request identifier.
        status: Execution outcome ("success" or "error").
        result: Tool execution result (on success).
        error_message: Error description (on failure).
    """

    request_id: str
    status: str
    result: dict[str, Any] | None = None
    error_message: str | None = None


def _message_tag(message_bytes: bytes, secret: bytes) -> bytes:
    """Return the HMAC tag for one raw message body."""
    return hmac.new(secret, message_bytes, hashlib.sha256).digest()


def _framed_payload(message_bytes: bytes, secret: bytes) -> bytes:
    """Return one message body followed by its authentication tag."""
    return message_bytes + _message_tag(message_bytes, secret)


def _split_framed_payload(frame: bytes) -> tuple[bytes, bytes]:
    """Split one framed payload into message body and received tag."""
    return frame[:-HMAC_TAG_LEN], frame[-HMAC_TAG_LEN:]


def sign_message(message_bytes: bytes, secret: bytes) -> bytes:
    """Sign a message with HMAC-SHA256.

    Args:
        message_bytes: JSON message body.
        secret: 32-byte shared secret.

    Returns:
        Length-prefixed frame: length(4) + body(N) + hmac(32).
    """
    payload = _framed_payload(message_bytes, secret)
    length_prefix = struct.pack(">I", len(payload))
    return length_prefix + payload


def verify_and_extract(frame: bytes, secret: bytes) -> bytes:
    """Verify HMAC and extract the message body from a frame.

    Args:
        frame: Raw frame bytes (body + hmac tag, without length prefix).
        secret: 32-byte shared secret.

    Returns:
        Verified JSON message body.

    Raises:
        AuthenticationError: If HMAC verification fails.
    """
    if len(frame) < HMAC_TAG_LEN:
        raise AuthenticationError("Frame too short for HMAC tag")

    message_bytes, received_tag = _split_framed_payload(frame)
    expected_tag = _message_tag(message_bytes, secret)

    if not hmac.compare_digest(received_tag, expected_tag):
        raise AuthenticationError("HMAC verification failed")

    return message_bytes
