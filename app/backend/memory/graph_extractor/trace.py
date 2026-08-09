"""JSONL decision trace for the graph extractor.

Mirrors the dialog-recorder/distiller trace pattern: one line per
decision, written best-effort to the runtime directory.
"""

from __future__ import annotations

import json
import logging
import os
from collections import deque
from datetime import UTC, datetime
from typing import Any


logger = logging.getLogger(__name__)

TRACE_FILE_NAME = "graph_extractor_trace.jsonl"

TRACE_DECISION_STORED = "assertion_stored"
TRACE_DECISION_DUPLICATE = "assertion_skipped_duplicate"
TRACE_DECISION_INVALIDATED = "assertion_invalidated_previous"
TRACE_DECISION_CONFLICT_KEPT = "assertion_conflict_kept_both"
TRACE_DECISION_SUBJECT_UNDECLARED = "assertion_skipped_subject_undeclared"
TRACE_DECISION_FORBIDDEN_ENTITY = "entity_skipped_forbidden_name"
TRACE_DECISION_PARSE_EMPTY = "extraction_empty_or_unparsed"
TRACE_DECISION_UNSAFE_OUTPUT = "assertion_skipped_unsafe_output"

DEFAULT_TRACE_LIMIT = 100
MAX_TRACE_LIMIT = 500


def _trace_path() -> str:
    """Return the trace file path inside the runtime directory."""
    runtime_dir = os.environ.get("BIMEMO_RUNTIME_DIR", ".runtime")
    return os.path.join(runtime_dir, TRACE_FILE_NAME)


def write_graph_extractor_trace(
    decision: str,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append one decision line to the extractor trace (best-effort)."""
    entry = {
        "timestamp": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3],
        "decision": decision,
        **(detail or {}),
    }
    try:
        path = _trace_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as trace_file:
            trace_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("Failed to write graph extractor trace", exc_info=True)


def read_graph_extractor_trace(
    limit: int = DEFAULT_TRACE_LIMIT,
    decision: str | None = None,
) -> list[dict[str, Any]]:
    """Read recent extractor trace entries, newest-first.

    Best-effort read of the local diagnostics JSONL file. A missing or
    unreadable file yields an empty list — the trace is diagnostic only.
    """
    capped = max(1, min(limit, MAX_TRACE_LIMIT))
    path = _trace_path()
    if not os.path.exists(path):
        return []
    entries: deque[dict[str, Any]] = deque(maxlen=capped)
    try:
        with open(path, encoding="utf-8") as trace_file:
            for line in trace_file:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    entry = json.loads(stripped)
                except json.JSONDecodeError:
                    logger.warning(
                        "Skipping malformed graph extractor trace line: %.200s",
                        stripped,
                    )
                    continue
                if decision and str(entry.get("decision", "")) != decision:
                    continue
                entries.append(entry)
    except OSError:
        logger.warning("Failed to read graph extractor trace", exc_info=True)
        return []
    return list(reversed(entries))
