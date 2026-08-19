"""Shared helpers for HTTP conditional requests (ETag / If-None-Match)."""

from __future__ import annotations


def if_none_match_matches(if_none_match: str | None, etag: str) -> bool:
    """Return whether one If-None-Match header matches one quoted ETag.

    Handles the ``*`` wildcard and comma-separated lists of entity tags;
    an absent or non-matching header is a non-match.

    Args:
        if_none_match: Raw If-None-Match header value (may be absent).
        etag: The quoted ETag to test against.

    Returns:
        True when the conditional header matches the candidate ETag.
    """
    if not if_none_match:
        return False
    if if_none_match.strip() == "*":
        return True
    return etag in {part.strip() for part in if_none_match.split(",")}
