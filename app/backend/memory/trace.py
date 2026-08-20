"""Shared best-effort JSONL trace for memory diagnostics.

One line per decision/event, appended to a JSONL file without failing
the caller: a missing or unreadable file yields an empty read, and an
unwritable path only logs a warning. Used by the dialog-recorder and
graph-extractor trace modules, which keep their own path resolution,
entry shapes, and public function names.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

DEFAULT_TRACE_LIMIT = 100
MAX_TRACE_LIMIT = 500


def _normalized_limit(limit: int) -> int:
    """Clamp one requested trace limit to the supported range."""
    return max(1, min(limit, MAX_TRACE_LIMIT))


class JsonlTrace:
    """A bounded JSONL trace file: best-effort append and newest-first read.

    The path is resolved through a zero-argument callable on every
    operation, so callers can point the trace at a different file
    (tests, environment changes) without rebuilding the instance.
    """

    def __init__(
        self,
        path: Callable[[], str | Path],
        *,
        label: str,
        default_limit: int = DEFAULT_TRACE_LIMIT,
        max_limit: int = MAX_TRACE_LIMIT,
    ) -> None:
        """Bind the trace to a lazily resolved path and a log label."""
        self._path = path
        self._label = label
        self._default_limit = default_limit
        self._max_limit = max_limit

    def _resolve_path(self) -> Path:
        """Return the current trace file path."""
        return Path(self._path())

    def append(self, entry: dict[str, Any]) -> None:
        """Append one trace entry as a JSON line (best-effort)."""
        trace_path = self._resolve_path()
        try:
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            with trace_path.open("a", encoding="utf-8") as trace_file:
                trace_file.write(json.dumps(entry, ensure_ascii=False))
                trace_file.write("\n")
        except OSError:
            logger.warning("Failed to append %s trace", self._label, exc_info=True)

    def read(
        self,
        limit: int | None = None,
        decision: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read recent trace entries in newest-first order.

        A missing or unreadable file yields an empty list; malformed
        lines are skipped with a warning. When ``decision`` is set,
        only entries whose ``decision`` field matches it are returned.
        """
        effective_limit = (
            self._default_limit if limit is None else _normalized_limit(limit)
        )
        trace_path = self._resolve_path()
        if not trace_path.exists():
            return []

        trace_entries: deque[dict[str, Any]] = deque(maxlen=effective_limit)
        try:
            with trace_path.open(encoding="utf-8") as trace_file:
                for line in trace_file:
                    stripped_line = line.strip()
                    if not stripped_line:
                        continue
                    entry = self._load_entry(stripped_line)
                    if entry is None:
                        continue
                    if decision and str(entry.get("decision", "")) != decision:
                        continue
                    trace_entries.append(entry)
        except OSError:
            logger.warning("Failed to read %s trace", self._label, exc_info=True)
            return []

        return list(reversed(trace_entries))

    def _load_entry(self, line: str) -> dict[str, Any] | None:
        """Parse one trace line, skipping malformed JSON."""
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            logger.warning(
                "Skipping malformed %s trace line: %.200s",
                self._label,
                line,
            )
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed
