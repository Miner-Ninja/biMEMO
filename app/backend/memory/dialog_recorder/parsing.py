"""Parsing helpers for dialog recorder model output."""

from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from backend.memory.models import Record, RecordKind

from .constants import (
    DIALOG_RECORDER_ACTION_ITEMS_HEADER,
    DIALOG_RECORDER_KEY_FACTS_HEADER,
    DIALOG_RECORDER_LIST_ITEM_PREFIX,
    DIALOG_RECORDER_MEMORY_KINDS_HEADER,
    DIALOG_RECORDER_SUMMARY_PREFIX,
)


logger = logging.getLogger(__name__)


def _extract_json(raw: str) -> str:
    """Extract JSON from LLM output that may contain thinking blocks."""
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return raw[start : end + 1]
    return raw


def _parse_record_kind(raw_kind: str) -> RecordKind | None:
    """Normalize one raw memory-kind label to a supported enum."""
    normalized_kind = raw_kind.strip().casefold()
    for record_kind in RecordKind:
        if record_kind.value == normalized_kind:
            return record_kind
    return None


def _section_for_line(line: str) -> str | None:
    """Return the dialog recorder section header for one line, if present."""
    if line == DIALOG_RECORDER_KEY_FACTS_HEADER:
        return DIALOG_RECORDER_KEY_FACTS_HEADER
    if line == DIALOG_RECORDER_ACTION_ITEMS_HEADER:
        return DIALOG_RECORDER_ACTION_ITEMS_HEADER
    if line == DIALOG_RECORDER_MEMORY_KINDS_HEADER:
        return DIALOG_RECORDER_MEMORY_KINDS_HEADER
    return None


def _list_item(line: str) -> str | None:
    """Return one dialog recorder list item without the list prefix."""
    if not line.startswith(DIALOG_RECORDER_LIST_ITEM_PREFIX):
        return None
    item = line.removeprefix(DIALOG_RECORDER_LIST_ITEM_PREFIX).strip()
    return item or None


def _append_record_item(
    current_section: str,
    item: str,
    key_facts: list[str],
    action_items: list[str],
    memory_kinds: list[RecordKind],
) -> None:
    """Append one parsed plain-text item to the active dialog recorder section."""
    if current_section == DIALOG_RECORDER_KEY_FACTS_HEADER:
        key_facts.append(item)
        return
    if current_section == DIALOG_RECORDER_ACTION_ITEMS_HEADER:
        action_items.append(item)
        return
    if current_section != DIALOG_RECORDER_MEMORY_KINDS_HEADER:
        return
    memory_kind = _parse_record_kind(item)
    if memory_kind is not None and memory_kind not in memory_kinds:
        memory_kinds.append(memory_kind)


def _parse_record_from_plain_text(raw: str) -> Record | None:
    """Parse dialog recorder plain-text output into a Record."""
    stripped_lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not stripped_lines:
        return None

    summary = ""
    key_facts: list[str] = []
    action_items: list[str] = []
    memory_kinds: list[RecordKind] = []
    current_section = ""

    for line in stripped_lines:
        if line.startswith(DIALOG_RECORDER_SUMMARY_PREFIX):
            summary = line.removeprefix(DIALOG_RECORDER_SUMMARY_PREFIX).strip()
            current_section = ""
            continue
        section = _section_for_line(line)
        if section is not None:
            current_section = section
            continue
        item = _list_item(line)
        if item is None:
            continue
        _append_record_item(
            current_section,
            item,
            key_facts,
            action_items,
            memory_kinds,
        )

    if not summary:
        return None

    return Record(
        summary=summary,
        key_facts=key_facts,
        action_items=action_items,
        memory_kinds=memory_kinds,
    )


def _parse_observation(raw: str) -> Record | None:
    """Parse raw LLM output into an Record."""
    parsed_observation = _parse_record_from_plain_text(raw)
    if parsed_observation is not None:
        return parsed_observation

    try:
        cleaned = _extract_json(raw)
        data = json.loads(cleaned)
        return Record.model_validate(data)
    except json.JSONDecodeError, ValidationError:
        logger.warning("Dialog recorder output failed validation: %.1000s", raw)
        return None
