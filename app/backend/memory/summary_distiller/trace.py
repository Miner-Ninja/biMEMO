"""File-based distiller pipeline trace for diagnostics."""

from __future__ import annotations

import json
import logging
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

TRACE_FILE_PATH = (
    Path(__file__).resolve().parents[3] / ".runtime" / "distiller_trace.jsonl"
)
DEFAULT_TRACE_LIMIT = 100
MAX_TRACE_LIMIT = 500


def _normalized_limit(limit: int) -> int:
    """Clamp one requested trace limit to the supported range."""
    return max(1, min(limit, MAX_TRACE_LIMIT))


def _trace_entries(limit: int) -> deque[dict[str, Any]]:
    """Create a bounded deque for trace entries."""
    return deque(maxlen=_normalized_limit(limit))


def _load_trace_entry(line: str) -> dict[str, Any] | None:
    """Parse one trace line, skipping malformed JSON."""
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        logger.warning(
            "Skipping malformed distiller trace line: %.200s",
            line,
        )
        return None


def _matches_decision(
    entry: dict[str, Any],
    decision: str | None,
) -> bool:
    """Return True when one trace entry matches the optional decision filter."""
    return not decision or str(entry.get("decision", "")) == decision


def _normalize_trace_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Return a trace entry with a stable timestamp field."""
    normalized = dict(entry)
    normalized.setdefault(
        "timestamp",
        datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
    )
    return normalized


def append_distiller_trace(entry: dict[str, Any]) -> None:
    """Append one distiller trace entry to the JSONL file."""
    normalized_entry = _normalize_trace_entry(entry)
    try:
        TRACE_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with TRACE_FILE_PATH.open("a", encoding="utf-8") as trace_file:
            trace_file.write(json.dumps(normalized_entry, ensure_ascii=False))
            trace_file.write("\n")
    except OSError:
        logger.warning("Failed to append distiller trace", exc_info=True)


def read_distiller_trace(
    limit: int = DEFAULT_TRACE_LIMIT,
    decision: str | None = None,
) -> list[dict[str, Any]]:
    """Read recent distiller trace entries in newest-first order."""
    if not TRACE_FILE_PATH.exists():
        return []

    trace_entries = _trace_entries(limit)
    try:
        with TRACE_FILE_PATH.open(encoding="utf-8") as trace_file:
            for line in trace_file:
                stripped_line = line.strip()
                if not stripped_line:
                    continue
                entry = _load_trace_entry(stripped_line)
                if entry is None:
                    continue
                if not _matches_decision(entry, decision):
                    continue
                trace_entries.append(entry)
    except OSError:
        logger.warning("Failed to read distiller trace", exc_info=True)
        return []

    return list(reversed(trace_entries))
