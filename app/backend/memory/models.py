"""Pydantic models for records and summaries."""

from enum import StrEnum

from pydantic import BaseModel, field_validator


# --- Field length limits ---
MAX_SUMMARY_LEN = 500
MAX_MAIN_POINT_LEN = 500
MAX_LIST_ITEMS = 10
MAX_ITEM_LEN = 200
MAX_RELATION_ITEMS = 50
MAX_MEMORY_KIND_ITEMS = 4
HIGH_CONFIDENCE_SCORE = 3
MEDIUM_CONFIDENCE_SCORE = 2
LOW_CONFIDENCE_SCORE = 1


class TrustLevel(StrEnum):
    """Trust level for a summary."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SummaryScope(StrEnum):
    """Time horizon for a summary."""

    LONG_TERM = "long_term"
    SHORT_TERM = "short_term"


class RecordRelationType(StrEnum):
    """Typed relation between a summary and a source record."""

    SUPPORTS = "supports"
    CONFLICTS = "conflicts"
    OUTDATES = "outdates"


class RecordKind(StrEnum):
    """Meaningful memory categories for dialog recorder output."""

    FACT = "fact"
    PREFERENCE = "preference"
    ACTION = "action"
    CONFLICT = "conflict"


TRUST_LEVEL_SCORE_MAP = {
    TrustLevel.HIGH: HIGH_CONFIDENCE_SCORE,
    TrustLevel.MEDIUM: MEDIUM_CONFIDENCE_SCORE,
    TrustLevel.LOW: LOW_CONFIDENCE_SCORE,
}


def _normalize_enum[EnumValue: StrEnum](
    raw_value: str,
    enum_type: type[EnumValue],
    default: EnumValue,
) -> EnumValue:
    """Normalize one raw string into a supported enum value."""
    try:
        return enum_type(raw_value)
    except ValueError:
        return default


def normalize_summary_scope(raw_scope: str) -> SummaryScope:
    """Normalize raw scope input to a supported summary scope."""
    return _normalize_enum(raw_scope, SummaryScope, SummaryScope.LONG_TERM)


def normalize_trust_level(raw_trust_level: str) -> TrustLevel:
    """Normalize raw trust input to a supported trust label.

    Args:
        raw_trust_level: Trust label.

    Returns:
        Normalized trust label.
    """
    return _normalize_enum(raw_trust_level, TrustLevel, TrustLevel.MEDIUM)


def get_trust_level_score(raw_trust_level: str) -> int:
    """Convert a trust label to a sortable numeric score.

    Args:
        raw_trust_level: Trust label.

    Returns:
        Sortable trust score.
    """
    normalized_trust_level = normalize_trust_level(raw_trust_level)
    return TRUST_LEVEL_SCORE_MAP[normalized_trust_level]


def _truncate(value: str, max_len: int) -> str:
    """Truncate a string to the specified length."""
    return value[:max_len] if len(value) > max_len else value


def _truncate_list(items: list[str], max_items: int, max_len: int) -> list[str]:
    """Truncate a list of strings to limits."""
    return [_truncate(item, max_len) for item in items[:max_items]]


def _truncate_enum_list[EnumValue: StrEnum](
    items: list[EnumValue],
    max_items: int,
) -> list[EnumValue]:
    """Limit an enum list to the supported maximum size."""
    return items[:max_items]


class Record(BaseModel):
    """Structured record from the Dialog Recorder agent.

    Attributes:
        summary: Concise summary of the compressed content.
        key_facts: Important facts extracted from messages.
        action_items: Actionable items identified.
        memory_kinds: Meaningful memory categories detected in the dialog step.
    """

    summary: str
    key_facts: list[str]
    action_items: list[str]
    memory_kinds: list[RecordKind] = []

    @field_validator("summary")
    @classmethod
    def truncate_summary(cls, v: str) -> str:
        """Truncate summary to MAX_SUMMARY_LEN."""
        return _truncate(v, MAX_SUMMARY_LEN)

    @field_validator("key_facts")
    @classmethod
    def truncate_key_facts(cls, v: list[str]) -> list[str]:
        """Truncate key_facts to limits."""
        return _truncate_list(v, MAX_LIST_ITEMS, MAX_ITEM_LEN)

    @field_validator("action_items")
    @classmethod
    def truncate_action_items(cls, v: list[str]) -> list[str]:
        """Truncate action_items to limits."""
        return _truncate_list(v, MAX_LIST_ITEMS, MAX_ITEM_LEN)

    @field_validator("memory_kinds")
    @classmethod
    def truncate_memory_kinds(
        cls,
        record_kinds: list[RecordKind],
    ) -> list[RecordKind]:
        """Limit memory kinds to the supported categories."""
        return _truncate_enum_list(record_kinds, MAX_MEMORY_KIND_ITEMS)


class Summary(BaseModel):
    """Structured summary from the Summary Distiller agent.

    Attributes:
        main_point: Identified main conclusion or theme.
        based_on: Linked records.
        trust_level: Trust level.
    """

    main_point: str
    based_on: list[str]
    trust_level: TrustLevel
    scope: SummaryScope = SummaryScope.LONG_TERM
    record_relations: list[RecordRelationType] = []

    @field_validator("main_point")
    @classmethod
    def truncate_main_point(cls, v: str) -> str:
        """Truncate main_point to MAX_PATTERN_LEN."""
        return _truncate(v, MAX_MAIN_POINT_LEN)

    @field_validator("based_on")
    @classmethod
    def truncate_based_on(cls, v: list[str]) -> list[str]:
        """Truncate based_on to limits."""
        return _truncate_list(v, MAX_LIST_ITEMS, MAX_ITEM_LEN)

    @field_validator("scope", mode="before")
    @classmethod
    def normalize_scope(cls, v: SummaryScope | str) -> SummaryScope:
        """Normalize summary scope values."""
        if isinstance(v, SummaryScope):
            return v
        return normalize_summary_scope(str(v))

    @field_validator("record_relations")
    @classmethod
    def truncate_record_relations(
        cls,
        relation_values: list[RecordRelationType],
    ) -> list[RecordRelationType]:
        """Limit relation labels to a reasonable record count."""
        return _truncate_enum_list(relation_values, MAX_RELATION_ITEMS)
