"""Relation normalization and conflict resolution for the graph extractor."""

from __future__ import annotations

from typing import Any

from .constants import FORBIDDEN_ENTITY_NAMES, RELATION_VOCABULARY


CONFLICT_INVALIDATE = "invalidate"
CONFLICT_SKIP_DUPLICATE = "skip_duplicate"
CONFLICT_KEEP_BOTH = "keep_both"

_INVALIDATING_TRUST_LEVELS = ("high", "medium")


def normalize_relation(relation: str) -> str:
    """Map one raw relation onto the closed starter vocabulary."""
    candidate = relation.strip().casefold().replace(" ", "_")
    if candidate in RELATION_VOCABULARY:
        return candidate
    return "relates_to"


def is_forbidden_entity_name(name: str) -> bool:
    """Return whether one entity name is on the junk-node blocklist."""
    return name.strip().casefold() in FORBIDDEN_ENTITY_NAMES


def _same_object(
    new_object_entity_id: str | None,
    new_object_value: str | None,
    existing: dict[str, Any],
) -> bool:
    """Return whether the new assertion targets the same object."""
    if new_object_entity_id is not None:
        return existing.get("object_entity_id") == new_object_entity_id
    existing_value = existing.get("object_value")
    if new_object_value is None or existing_value is None:
        return new_object_value == existing_value
    return new_object_value.strip().casefold() == str(
        existing_value
    ).strip().casefold()


def resolve_conflict_action(
    *,
    new_relation: str,
    new_trust: str,
    new_object_entity_id: str | None,
    new_object_value: str | None,
    existing_valid: list[dict[str, Any]],
) -> tuple[str, str | None]:
    """Decide what to do with one new assertion against stored valid ones.

    Returns (action, existing_assertion_id):
    - skip_duplicate — same subject+relation+object already stored;
    - invalidate — same subject+relation, different object, confident new
      assertion supersedes the existing one;
    - keep_both — anything else (including low-trust contradictions).
    """
    for existing in existing_valid:
        if existing.get("relation") != new_relation:
            continue
        if _same_object(new_object_entity_id, new_object_value, existing):
            return CONFLICT_SKIP_DUPLICATE, existing.get("id")
        if new_trust in _INVALIDATING_TRUST_LEVELS:
            return CONFLICT_INVALIDATE, existing.get("id")
        return CONFLICT_KEEP_BOTH, existing.get("id")
    return CONFLICT_KEEP_BOTH, None
