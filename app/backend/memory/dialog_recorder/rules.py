"""Heuristics and duplicate checks used by the dialog recorder pipeline."""

from __future__ import annotations

import json

from backend.memory.models import Record, RecordKind

from .constants import (
    ACTION_KEYWORDS,
    CONFLICT_KEYWORDS,
    CYRILLIC_CODEPOINT_END,
    CYRILLIC_CODEPOINT_START,
    ENGLISH_CONFLICT_ACTION_TEMPLATE,
    ENGLISH_FOLLOW_UP_ACTION_TEMPLATE,
    ENGLISH_PREFERENCE_ACTION_TEMPLATE,
    GENERIC_SUMMARY_PHRASES,
    MAX_SYNTHETIC_ACTION_ITEMS,
    MEMORY_BOUNDARY_MARKERS,
    MEMORY_RECAP_QUESTION_MARKERS,
    MEMORY_RECAP_SCOPE_MARKERS,
    MIN_DUPLICATE_TOKEN_OVERLAP,
    PREFERENCE_KEYWORDS,
    ROLE_PREFIX_REPLACEMENTS_FOR_FILTER,
    ROLE_PREFIXES_FOR_FILTER,
    RUSSIAN_CONFLICT_ACTION_TEMPLATE,
    RUSSIAN_FOLLOW_UP_ACTION_TEMPLATE,
    RUSSIAN_PREFERENCE_ACTION_TEMPLATE,
)


def build_record_payload_text(record: Record) -> str:
    """Build a richer text payload for embeddings and downstream synthesis."""
    lines = [f"Summary: {record.summary}"]
    if record.key_facts:
        lines.append("Key facts:")
        lines.extend(f"- {fact}" for fact in record.key_facts)
    if record.action_items:
        lines.append("Action items:")
        lines.extend(f"- {item}" for item in record.action_items)
    return "\n".join(lines)


def _normalize_role_labels_for_filter(messages: list[str]) -> str:
    """Normalize dialog recorder role prefixes before security scanning."""
    normalized_messages: list[str] = []
    for message in messages:
        normalized_lines: list[str] = []
        for line in message.splitlines():
            stripped_line = line
            for prefix in ROLE_PREFIXES_FOR_FILTER:
                if stripped_line.startswith(prefix):
                    stripped_line = (
                        ROLE_PREFIX_REPLACEMENTS_FOR_FILTER[prefix]
                        + " "
                        + stripped_line[len(prefix) :].lstrip()
                    )
                    break
            normalized_lines.append(stripped_line)
        normalized_messages.append("\n".join(normalized_lines))
    return "\n---\n".join(normalized_messages)


def _contains_any_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    """Return True when any keyword is present in the normalized text."""
    normalized = text.lower()
    return any(keyword in normalized for keyword in keywords)


def _infer_memory_kinds(
    record: Record,
    messages: list[str],
) -> list[RecordKind]:
    """Infer memory kinds when the model omits or under-specifies them."""
    combined_text = "\n".join(
        [
            record.summary,
            *record.key_facts,
            *record.action_items,
            *messages,
        ]
    ).lower()
    memory_kinds: list[RecordKind] = []

    if record.key_facts:
        memory_kinds.append(RecordKind.FACT)
    if record.action_items or _contains_any_keyword(
        combined_text,
        ACTION_KEYWORDS,
    ):
        memory_kinds.append(RecordKind.ACTION)
    if _contains_any_keyword(combined_text, PREFERENCE_KEYWORDS):
        memory_kinds.append(RecordKind.PREFERENCE)
    if _contains_any_keyword(combined_text, CONFLICT_KEYWORDS):
        memory_kinds.append(RecordKind.CONFLICT)

    deduplicated: list[RecordKind] = []
    for memory_kind in memory_kinds:
        if memory_kind not in deduplicated:
            deduplicated.append(memory_kind)
    return deduplicated


def _normalize_memory_kinds(
    record: Record,
    messages: list[str],
) -> Record:
    """Normalize record memory kinds with a heuristic fallback."""
    memory_kinds = (
        record.memory_kinds
        if record.memory_kinds
        else _infer_memory_kinds(record, messages)
    )
    return Record(
        summary=record.summary,
        key_facts=record.key_facts,
        action_items=record.action_items,
        memory_kinds=memory_kinds,
    )


def _contains_cyrillic(text: str) -> bool:
    """Return True when the text contains Cyrillic characters."""
    return any(
        CYRILLIC_CODEPOINT_START <= ord(character.lower()) <= CYRILLIC_CODEPOINT_END
        for character in text
    )


def _action_template(
    *,
    use_russian: bool,
    russian_template: str,
    english_template: str,
) -> str:
    """Return the language-specific synthetic action template."""
    return russian_template if use_russian else english_template


def _formatted_synthetic_action(
    *,
    use_russian: bool,
    russian_template: str,
    english_template: str,
    summary: str,
) -> str:
    """Format one synthetic action item in the detected language."""
    return _action_template(
        use_russian=use_russian,
        russian_template=russian_template,
        english_template=english_template,
    ).format(summary=summary)


def _build_synthetic_action_items(
    record: Record,
    messages: list[str],
) -> list[str]:
    """Build fallback action items for memory-worthy records."""
    if record.action_items:
        return record.action_items

    combined_text = "\n".join([record.summary, *messages])
    use_russian = _contains_cyrillic(combined_text)
    synthetic_action_items: list[str] = []

    if RecordKind.PREFERENCE in record.memory_kinds:
        synthetic_action_items.append(
            _formatted_synthetic_action(
                use_russian=use_russian,
                russian_template=RUSSIAN_PREFERENCE_ACTION_TEMPLATE,
                english_template=ENGLISH_PREFERENCE_ACTION_TEMPLATE,
                summary=record.summary,
            )
        )
    if RecordKind.ACTION in record.memory_kinds:
        follow_up_source = record.key_facts[0] if record.key_facts else record.summary
        synthetic_action_items.append(
            _formatted_synthetic_action(
                use_russian=use_russian,
                russian_template=RUSSIAN_FOLLOW_UP_ACTION_TEMPLATE,
                english_template=ENGLISH_FOLLOW_UP_ACTION_TEMPLATE,
                summary=follow_up_source,
            )
        )
    if (
        RecordKind.CONFLICT in record.memory_kinds
        and len(synthetic_action_items) < MAX_SYNTHETIC_ACTION_ITEMS
    ):
        synthetic_action_items.append(
            _formatted_synthetic_action(
                use_russian=use_russian,
                russian_template=RUSSIAN_CONFLICT_ACTION_TEMPLATE,
                english_template=ENGLISH_CONFLICT_ACTION_TEMPLATE,
                summary=record.summary,
            )
        )

    deduplicated_action_items: list[str] = []
    for action_item in synthetic_action_items:
        normalized_action_item = action_item.strip()
        if (
            normalized_action_item
            and normalized_action_item not in deduplicated_action_items
        ):
            deduplicated_action_items.append(normalized_action_item)
    return deduplicated_action_items[:MAX_SYNTHETIC_ACTION_ITEMS]


def _enrich_observation(
    record: Record,
    messages: list[str],
) -> Record:
    """Fill missing dialog recorder fields with lightweight heuristics."""
    normalized_observation = _normalize_memory_kinds(record, messages)
    return Record(
        summary=normalized_observation.summary,
        key_facts=normalized_observation.key_facts,
        action_items=_build_synthetic_action_items(normalized_observation, messages),
        memory_kinds=normalized_observation.memory_kinds,
    )


def _normalize_text_tokens(text: str) -> set[str]:
    """Return normalized alphanumeric tokens for lightweight overlap checks."""
    normalized = "".join(
        character.lower() if character.isalnum() else " " for character in text
    )
    return {token for token in normalized.split() if len(token) > 2}


def _string_items(items: list[object]) -> list[str]:
    """Return only the string items from one raw list payload."""
    return [item for item in items if isinstance(item, str)]


def _normalize_string_list(raw_items: object) -> list[str]:
    """Normalize raw list-like data that may arrive as JSON text."""
    if isinstance(raw_items, list):
        return _string_items(raw_items)
    if isinstance(raw_items, str):
        try:
            parsed_items = json.loads(raw_items)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed_items, list):
            return _string_items(parsed_items)
    return []


def _token_overlap_ratio(left: str, right: str) -> float:
    """Return the overlap ratio between two normalized token sets."""
    left_tokens = _normalize_text_tokens(left)
    right_tokens = _normalize_text_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    shared = left_tokens & right_tokens
    return len(shared) / min(len(left_tokens), len(right_tokens))


def _record_signature(record: Record) -> str:
    """Build a text signature for duplicate detection."""
    parts = [
        record.summary,
        *record.key_facts,
        *record.action_items,
        *[memory_kind.value for memory_kind in record.memory_kinds],
    ]
    return "\n".join(part for part in parts if part)


def _extract_role_lines(messages: list[str], role_prefix: str) -> list[str]:
    """Extract role-specific lines from the batched dialog recorder message text."""
    extracted_lines: list[str] = []
    for message in messages:
        for line in message.splitlines():
            stripped_line = line.strip()
            if stripped_line.startswith(role_prefix):
                extracted_lines.append(stripped_line[len(role_prefix) :].strip())
    return extracted_lines


def _role_text(messages: list[str], role_prefix: str) -> str:
    """Join extracted role lines into one normalized role text block."""
    return " ".join(_extract_role_lines(messages, role_prefix)).lower()


def _is_memory_recap_batch(messages: list[str]) -> bool:
    """Return True when the batch is only a memory self-check or boundary check."""
    user_text = _role_text(messages, "User:")
    assistant_text = _role_text(messages, "Assistant:")
    if not user_text or not assistant_text:
        return False
    asks_for_recap = any(
        marker in user_text for marker in MEMORY_RECAP_QUESTION_MARKERS
    )
    mentions_memory_scope = any(
        marker in user_text for marker in MEMORY_RECAP_SCOPE_MARKERS
    )
    combined_text = "\n".join((user_text, assistant_text))
    mentions_memory_boundary = any(
        marker in combined_text for marker in MEMORY_BOUNDARY_MARKERS
    )
    return asks_for_recap or mentions_memory_scope or mentions_memory_boundary


def _is_memory_worthy_observation(
    record: Record,
    messages: list[str],
) -> bool:
    """Return True when the record contains meaningful new memory."""
    if _is_memory_recap_batch(messages):
        return False
    if not record.memory_kinds:
        return False
    if (
        record.memory_kinds == [RecordKind.FACT]
        and not record.action_items
        and not record.key_facts
    ):
        return False
    summary_lower = record.summary.lower()
    return not (
        not record.action_items
        and not record.key_facts
        and any(phrase in summary_lower for phrase in GENERIC_SUMMARY_PHRASES)
    )


def _extract_summary_main_point(summary: object) -> str:
    """Extract a summary main point from dict or tuple payloads."""
    if isinstance(summary, dict):
        return str(summary.get("main_point", ""))
    if isinstance(summary, tuple) and len(summary) >= 2:
        return str(summary[1])
    return ""


def _is_duplicate_memory(
    record: Record,
    recent_observations: list[Record],
    recent_reflections: list[object],
) -> bool:
    """Return True when the record is already covered by memory."""
    candidate_signature = _record_signature(record)
    for recent_observation in recent_observations:
        if (
            _token_overlap_ratio(
                candidate_signature,
                _record_signature(recent_observation),
            )
            >= MIN_DUPLICATE_TOKEN_OVERLAP
        ):
            return True
    for summary in recent_reflections:
        main_point = _extract_summary_main_point(summary)
        if (
            main_point
            and _token_overlap_ratio(candidate_signature, main_point)
            >= MIN_DUPLICATE_TOKEN_OVERLAP
        ):
            return True
    return False
