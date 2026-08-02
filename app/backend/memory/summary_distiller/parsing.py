"""Parsing helpers for summary distiller model output."""

from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from backend.memory.models import (
    Summary,
    SummaryScope,
    normalize_summary_scope,
    normalize_trust_level,
)

from .constants import (
    DISTILLER_BASED_ON_HEADER,
    DISTILLER_LIST_ITEM_PREFIX,
    DISTILLER_MAIN_POINT_PREFIX,
    DISTILLER_SCOPE_PREFIX,
    DISTILLER_TRUST_LEVEL_PREFIX,
)


logger = logging.getLogger(__name__)


def _extract_json(raw: str) -> str:
    """Extract JSON from LLM output that may contain thinking blocks."""
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return raw[start : end + 1]
    return raw


def _based_on_item(line: str) -> str | None:
    """Return one distiller based_on item without the list prefix."""
    if not line.startswith(DISTILLER_LIST_ITEM_PREFIX):
        return None
    item = line.removeprefix(DISTILLER_LIST_ITEM_PREFIX).strip()
    return item or None


def _prefixed_value(line: str, prefix: str) -> str | None:
    """Return the stripped value for one known distiller line prefix."""
    if not line.startswith(prefix):
        return None
    return line.removeprefix(prefix).strip()


def _summary_field_values(raw: str) -> tuple[str, str, str, list[str]]:
    """Extract main_point, trust, scope, and based_on values from plain text."""
    stripped_lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not stripped_lines:
        return "", "", SummaryScope.LONG_TERM.value, []

    main_point = ""
    trust_level = ""
    scope = SummaryScope.LONG_TERM.value
    based_on: list[str] = []
    in_based_on_section = False

    for line in stripped_lines:
        if (
            main_point_value := _prefixed_value(
                line,
                DISTILLER_MAIN_POINT_PREFIX,
            )
        ) is not None:
            main_point = main_point_value
            in_based_on_section = False
            continue
        if (
            trust_level_value := _prefixed_value(line, DISTILLER_TRUST_LEVEL_PREFIX)
        ) is not None:
            trust_level = trust_level_value
            in_based_on_section = False
            continue
        if (scope_value := _prefixed_value(line, DISTILLER_SCOPE_PREFIX)) is not None:
            scope = scope_value
            in_based_on_section = False
            continue
        if line == DISTILLER_BASED_ON_HEADER:
            in_based_on_section = True
            continue
        if not in_based_on_section:
            continue
        item = _based_on_item(line)
        if item is not None:
            based_on.append(item)

    return main_point, trust_level, scope, based_on


def _parse_summary_from_plain_text(raw: str) -> Summary | None:
    """Parse distiller plain-text output into a Summary."""
    main_point, trust_level, scope, based_on = _summary_field_values(raw)

    if not main_point:
        return None

    return Summary(
        main_point=main_point,
        based_on=based_on,
        trust_level=normalize_trust_level(trust_level or "medium"),
        scope=normalize_summary_scope(scope),
        record_relations=[],
    )


def _parse_distillation(raw: str) -> Summary | None:
    """Parse raw LLM output into a Summary."""
    parsed_distillation = _parse_summary_from_plain_text(raw)
    if parsed_distillation is not None:
        return parsed_distillation

    try:
        cleaned = _extract_json(raw)
        data = json.loads(cleaned)
        return Summary.model_validate(data)
    except json.JSONDecodeError, ValidationError:
        logger.warning("Distiller output failed validation: %.200s", raw)
        return None
