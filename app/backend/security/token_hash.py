"""Shared bearer-token hashing.

One SHA-256 helper used by both the agent-token issuing route (agents.py)
and the MCP bearer validation (mcp.py), so the two never drift.
"""

from __future__ import annotations

import hashlib


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of one bearer token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
