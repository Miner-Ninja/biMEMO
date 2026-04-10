"""Rule helpers used by the summary distiller pipeline."""

from __future__ import annotations

from backend.memory.models import (
    Record,
    RecordKind,
    RecordRelationType,
    Summary,
    SummaryScope,
)
from backend.memory.time_signals import TimeSignals

from .constants import (
    BASED_ON_TOKEN_PATTERN,
    CONCISE_BASELINE_MARKERS,
    CONCISE_PREFERENCE_MARKERS,
    DEBUGGING_MARKERS,
    DEFAULT_PREFERENCE_MARKERS,
    DETAILED_PREFERENCE_MARKERS,
    FUTURE_SCOPE_MARKERS,
    MIN_BASED_ON_TOKEN_LENGTH,
    MIN_DUPLICATE_BASED_ON_TOKEN_OVERLAP,
    MIN_DUPLICATE_DISTILLATION_TOKEN_OVERLAP,
    NEGATED_CONCISE_MARKERS,
    NEGATED_DETAILED_MARKERS,
    NO_DURABLE_PREFERENCE_MARKERS,
    SEPARATION_MARKERS,
    SHORT_TERM_DISTILLATION_MARKERS,
    SHORT_TERM_OBSERVATION_MARKERS,
    TEMPORARY_PREFERENCE_MARKERS,
)


def _build_support_relations(record_ids: list[str]) -> list[dict[str, str]]:
    """Build default typed support relations for source records."""
    return [
        {
            "record_id": record_id,
            "relation": RecordRelationType.SUPPORTS.value,
        }
        for record_id in record_ids
    ]


def _extract_overlap_tokens(text: str) -> list[str]:
    """Extract comparable tokens for short-text overlap checks."""
    return [
        token
        for token in BASED_ON_TOKEN_PATTERN.findall(text.casefold())
        if len(token) >= MIN_BASED_ON_TOKEN_LENGTH
    ]


def _token_overlap_ratio(left: str, right: str) -> float:
    """Return a simple overlap score between two short texts."""
    left_tokens = set(_extract_overlap_tokens(left))
    right_tokens = set(_extract_overlap_tokens(right))
    if not left_tokens or not right_tokens:
        return 0.0
    shared = left_tokens & right_tokens
    return len(shared) / min(len(left_tokens), len(right_tokens))


def _contains_marker(text: str, markers: tuple[str, ...]) -> bool:
    """Return True when any normalized marker appears in the text."""
    normalized = text.casefold()
    return any(marker.casefold() in normalized for marker in markers)


def _summary_semantic_signature(main_point: str) -> tuple[str, ...]:
    """Build a language-neutral signature for common preference summaries."""
    lowered = main_point.casefold()
    prefers_detailed = _contains_marker(lowered, DETAILED_PREFERENCE_MARKERS)
    prefers_concise = _contains_marker(lowered, CONCISE_PREFERENCE_MARKERS)
    negates_concise = _contains_marker(lowered, NEGATED_CONCISE_MARKERS)
    negates_detailed = _contains_marker(lowered, NEGATED_DETAILED_MARKERS)
    mentions_debugging = _contains_marker(lowered, DEBUGGING_MARKERS)
    mentions_future_scope = _contains_marker(lowered, FUTURE_SCOPE_MARKERS)

    signature: list[str] = []
    if prefers_detailed and (prefers_concise or negates_concise):
        signature.append("pref_shift_concise_to_detailed")
    elif prefers_concise and (prefers_detailed or negates_detailed):
        signature.append("pref_shift_detailed_to_concise")
    elif prefers_detailed:
        signature.append("pref_detailed")
    elif prefers_concise:
        signature.append("pref_concise")

    if mentions_debugging:
        signature.append("scope_debugging")
    if mentions_future_scope:
        signature.append("scope_future")

    return tuple(signature)


def _main_point_matches_by_signature(
    candidate_main_point: str,
    recent_main_point: str,
) -> bool:
    """Return True when two summary main points share the same signature."""
    candidate_signature = _summary_semantic_signature(candidate_main_point)
    recent_signature = _summary_semantic_signature(recent_main_point)
    return bool(
        candidate_signature
        and recent_signature
        and candidate_signature == recent_signature
    )


def _main_point_matches_by_overlap(
    candidate_main_point: str,
    recent_main_point: str,
) -> bool:
    """Return True when two summary main points overlap enough by tokens."""
    return (
        recent_main_point
        and _token_overlap_ratio(candidate_main_point, recent_main_point)
        >= MIN_DUPLICATE_DISTILLATION_TOKEN_OVERLAP
    )


def _is_matching_summary_main_point(
    candidate_main_point: str,
    recent_main_point: str,
) -> bool:
    """Return True when a recent main point matches the candidate summary."""
    return _main_point_matches_by_signature(
        candidate_main_point,
        recent_main_point,
    ) or _main_point_matches_by_overlap(
        candidate_main_point,
        recent_main_point,
    )


def _is_duplicate_summary_main_point(
    main_point: str,
    recent_main_points: list[str],
) -> bool:
    """Return True when the new summary repeats recent live memory."""
    for recent_main_point in recent_main_points:
        if _is_matching_summary_main_point(main_point, recent_main_point):
            return True
    return False


def _find_duplicate_summary_record(
    main_point: str,
    recent_distillations: list[dict[str, object]],
) -> dict[str, object] | None:
    """Return the recent summary that matches the new main point best."""
    for summary in recent_distillations:
        recent_main_point = str(summary.get("main_point", "")).strip()
        if not recent_main_point:
            continue
        if _is_matching_summary_main_point(main_point, recent_main_point):
            return summary
    return None


def _normalize_reason_text(reason: str) -> str:
    """Normalize one summary reason for duplicate detection."""
    collapsed = " ".join(reason.split())
    return collapsed.strip(" -:;,.")


def _is_duplicate_reason(left: str, right: str) -> bool:
    """Return True when two reasons carry near-identical evidence."""
    normalized_left = _normalize_reason_text(left).casefold()
    normalized_right = _normalize_reason_text(right).casefold()
    if not normalized_left or not normalized_right:
        return False
    if normalized_left == normalized_right:
        return True

    left_tokens = set(_extract_overlap_tokens(normalized_left))
    right_tokens = set(_extract_overlap_tokens(normalized_right))
    if not left_tokens or not right_tokens:
        return False
    if left_tokens <= right_tokens or right_tokens <= left_tokens:
        return True

    return (
        _token_overlap_ratio(normalized_left, normalized_right)
        >= MIN_DUPLICATE_BASED_ON_TOKEN_OVERLAP
    )


def _reason_detail_score(reason: str) -> tuple[int, int]:
    """Score one reason so richer wording wins duplicate replacement."""
    normalized_reason = _normalize_reason_text(reason)
    return (
        len(_extract_overlap_tokens(normalized_reason)),
        len(normalized_reason),
    )


def _find_duplicate_reason_index(
    normalized_reason: str,
    compressed_reasons: list[str],
) -> int | None:
    """Return the matching duplicate index for one normalized reason."""
    for index, existing_reason in enumerate(compressed_reasons):
        if _is_duplicate_reason(normalized_reason, existing_reason):
            return index
    return None


def _merge_reason_into_compressed_list(
    compressed_reasons: list[str],
    normalized_reason: str,
) -> None:
    """Insert or replace one normalized reason in the compressed list."""
    duplicate_index = _find_duplicate_reason_index(
        normalized_reason,
        compressed_reasons,
    )
    if duplicate_index is None:
        compressed_reasons.append(normalized_reason)
        return

    existing_reason = compressed_reasons[duplicate_index]
    if _reason_detail_score(normalized_reason) > _reason_detail_score(existing_reason):
        compressed_reasons[duplicate_index] = normalized_reason


def _compress_based_on_reasons(reasons: list[str]) -> list[str]:
    """Drop near-duplicate reasons while keeping the richer wording."""
    compressed_reasons: list[str] = []
    for raw_reason in reasons:
        normalized_reason = _normalize_reason_text(raw_reason)
        if not normalized_reason:
            continue
        _merge_reason_into_compressed_list(
            compressed_reasons,
            normalized_reason,
        )
    return compressed_reasons


def _observations_contain_durable_preference_signal(
    records: list[Record],
) -> bool:
    """Return True when source records still carry a durable preference signal."""
    record_text = " ".join(
        " ".join(
            [
                record.summary,
                *record.key_facts,
                *record.action_items,
            ]
        )
        for record in records
    ).casefold()
    has_preference_memory = any(
        RecordKind.PREFERENCE in record.memory_kinds for record in records
    )
    mentions_future_preference = _contains_marker(
        record_text,
        FUTURE_SCOPE_MARKERS + DEFAULT_PREFERENCE_MARKERS + SEPARATION_MARKERS,
    )
    mentions_detailed_preference = _contains_marker(
        record_text,
        DETAILED_PREFERENCE_MARKERS,
    )
    mentions_concise_replacement = _contains_marker(
        record_text,
        NEGATED_CONCISE_MARKERS,
    )
    return has_preference_memory and (
        mentions_future_preference
        or (mentions_detailed_preference and mentions_concise_replacement)
    )


def _summary_text(summary: Summary) -> str:
    """Return the normalized combined summary text."""
    return " ".join([summary.main_point, *summary.based_on]).casefold()


def _record_text(records: list[Record]) -> str:
    """Return the normalized combined source-record text."""
    return " ".join(
        " ".join(
            [
                record.summary,
                *record.key_facts,
                *record.action_items,
            ]
        )
        for record in records
    ).casefold()


def _records_are_short_term_without_preferences(records: list[Record]) -> bool:
    """Return True when records look short-term and carry no preference memory."""
    combined_record_text = _record_text(records)
    has_short_term_markers = any(
        marker in combined_record_text for marker in SHORT_TERM_OBSERVATION_MARKERS
    )
    has_preference_kinds = any(
        RecordKind.PREFERENCE in record.memory_kinds for record in records
    )
    return has_short_term_markers and not has_preference_kinds


def _records_are_action_or_fact_only(records: list[Record]) -> bool:
    """Return True when records contain only action/fact memory kinds."""
    return all(
        RecordKind.PREFERENCE not in record.memory_kinds for record in records
    ) and any(
        RecordKind.ACTION in record.memory_kinds
        or RecordKind.FACT in record.memory_kinds
        for record in records
    )


def _infer_summary_scope(
    summary: Summary,
    records: list[Record] | None = None,
) -> SummaryScope:
    """Infer the durable time horizon from summary content and source signals."""
    scope_text = _summary_text(summary)
    mentions_short_term_markers = any(
        marker in scope_text for marker in SHORT_TERM_DISTILLATION_MARKERS
    )
    mentions_durable_preference_signal = _contains_marker(
        scope_text,
        FUTURE_SCOPE_MARKERS + DEFAULT_PREFERENCE_MARKERS + SEPARATION_MARKERS,
    ) or (
        _contains_marker(scope_text, DETAILED_PREFERENCE_MARKERS)
        and _contains_marker(scope_text, NEGATED_CONCISE_MARKERS)
    )
    negates_durable_preference = _contains_marker(
        scope_text,
        NO_DURABLE_PREFERENCE_MARKERS,
    )
    if (
        records
        and _observations_contain_durable_preference_signal(records)
        and mentions_durable_preference_signal
        and not negates_durable_preference
    ):
        return SummaryScope.LONG_TERM
    if mentions_short_term_markers:
        return SummaryScope.SHORT_TERM
    if records:
        if _records_are_short_term_without_preferences(records):
            return SummaryScope.SHORT_TERM
        if _records_are_action_or_fact_only(records):
            return SummaryScope.SHORT_TERM
    return summary.scope


def _is_overreaching_short_term_distillation(
    summary: Summary,
    records: list[Record],
) -> bool:
    """Return True when short-term context wrongly erases a durable preference."""
    if summary.scope is not SummaryScope.SHORT_TERM:
        return False
    summary_text = _summary_text(summary)
    negates_durable_preference = _contains_marker(
        summary_text,
        NO_DURABLE_PREFERENCE_MARKERS,
    )
    if not negates_durable_preference:
        return False
    return _observations_contain_durable_preference_signal(records)


def _is_overreaching_reversion_distillation(
    summary: Summary,
    records: list[Record],
) -> bool:
    """Return True when a summary restores an old concise baseline."""
    if not _observations_contain_durable_preference_signal(records):
        return False

    summary_text = _summary_text(summary)
    mentions_detailed = _contains_marker(
        summary_text,
        DETAILED_PREFERENCE_MARKERS,
    )
    mentions_concise = _contains_marker(
        summary_text,
        CONCISE_PREFERENCE_MARKERS,
    )
    marks_detailed_as_temporary = _contains_marker(
        summary_text,
        TEMPORARY_PREFERENCE_MARKERS + SHORT_TERM_DISTILLATION_MARKERS,
    )
    restores_concise_as_default = _contains_marker(
        summary_text,
        CONCISE_BASELINE_MARKERS,
    )
    return (
        mentions_detailed
        and mentions_concise
        and marks_detailed_as_temporary
        and restores_concise_as_default
    )


def _summary_mentions_preference_content(text: str) -> bool:
    """Return True when summary text talks about answer-style preferences."""
    return (
        _contains_marker(
            text,
            DETAILED_PREFERENCE_MARKERS
            + CONCISE_PREFERENCE_MARKERS
            + DEFAULT_PREFERENCE_MARKERS,
        )
        or "preference" in text
        or "style" in text
    )


def _should_downgrade_long_term_scope_by_time_signals(
    summary: Summary,
    records: list[Record],
) -> bool:
    """Return True when a long-term summary frames a preference as temporary."""
    if summary.scope is not SummaryScope.LONG_TERM:
        return False
    if not _observations_contain_durable_preference_signal(records):
        return False

    summary_text = _summary_text(summary)
    main_point_text = summary.main_point.casefold()
    if not _summary_mentions_preference_content(summary_text):
        return False

    main_point_has_temporary_signal = TimeSignals.text_contains_any(
        main_point_text,
        TimeSignals.TEMPORARY_CONTEXT_KEYS,
    )
    summary_has_non_persistent_signal = TimeSignals.text_contains_any(
        summary_text,
        TimeSignals.NON_PERSISTENT_KEYS,
    )
    summary_mentions_session_identifier = any(
        marker in summary_text
        for marker in (
            "memory-loop-",
            "session id",
            "session identifier",
            "validation run",
            "test run",
        )
    )
    return summary_has_non_persistent_signal or (
        main_point_has_temporary_signal
        and _summary_mentions_preference_content(main_point_text)
        and ("persist" in summary_text or summary_mentions_session_identifier)
    )


def _build_relation_heuristics(
    records: list[Record],
    summary: Summary,
) -> list[RecordRelationType]:
    """Infer relation labels when the model does not supply them."""
    summary_text = " ".join([summary.main_point, *summary.based_on]).casefold()
    summary_prefers_detailed = _contains_marker(
        summary_text,
        DETAILED_PREFERENCE_MARKERS,
    )
    summary_prefers_concise = _contains_marker(
        summary_text,
        CONCISE_PREFERENCE_MARKERS,
    )

    relation_values: list[RecordRelationType] = []
    for record in records:
        record_text = " ".join(
            [
                record.summary,
                *record.key_facts,
                *record.action_items,
            ]
        ).casefold()
        mentions_detailed = _contains_marker(
            record_text,
            DETAILED_PREFERENCE_MARKERS,
        )
        mentions_concise = _contains_marker(
            record_text,
            CONCISE_PREFERENCE_MARKERS,
        )
        negates_concise = _contains_marker(
            record_text,
            NEGATED_CONCISE_MARKERS,
        )
        negates_detailed = _contains_marker(
            record_text,
            NEGATED_DETAILED_MARKERS,
        )

        relation = RecordRelationType.SUPPORTS
        if summary_prefers_detailed:
            if mentions_concise and not negates_concise:
                relation = RecordRelationType.OUTDATES
            elif negates_detailed:
                relation = RecordRelationType.CONFLICTS
        elif summary_prefers_concise:
            if mentions_detailed and not negates_detailed:
                relation = RecordRelationType.OUTDATES
            elif negates_concise:
                relation = RecordRelationType.CONFLICTS

        relation_values.append(relation)

    return relation_values


def _build_typed_relations(
    record_ids: list[str],
    relation_values: list[RecordRelationType | str] | None,
    *,
    records: list[Record] | None = None,
    summary: Summary | None = None,
) -> list[dict[str, str]]:
    """Build stored record relations from distiller output."""
    if relation_values is None or len(relation_values) != len(record_ids):
        if records is not None and summary is not None:
            inferred_relations = _build_relation_heuristics(records, summary)
            if len(inferred_relations) == len(record_ids):
                relation_values = inferred_relations
        if relation_values is None or len(relation_values) != len(record_ids):
            return _build_support_relations(record_ids)
    return [
        {
            "record_id": record_id,
            "relation": (
                relation_value.value
                if isinstance(relation_value, RecordRelationType)
                else str(relation_value)
            ),
        }
        for record_id, relation_value in zip(
            record_ids,
            relation_values,
            strict=True,
        )
    ]


def _build_summary_input(records: list[Record]) -> str:
    """Build structured distiller input from full record payloads."""
    sections: list[str] = []
    for index, record in enumerate(records, start=1):
        lines = [f"[Record {index}]", f"Summary: {record.summary}"]
        if record.memory_kinds:
            memory_kind_text = ", ".join(
                memory_kind.value for memory_kind in record.memory_kinds
            )
            lines.append(f"Memory kinds: {memory_kind_text}")
        if record.key_facts:
            lines.append("Key facts:")
            lines.extend(f"- {fact}" for fact in record.key_facts)
        if record.action_items:
            lines.append("Action items:")
            lines.extend(f"- {item}" for item in record.action_items)
        sections.append("\n".join(lines))
    return "\n\n---\n\n".join(sections)
