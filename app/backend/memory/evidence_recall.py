"""Helpers for narrow evidence-backed recall questions."""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from backend.memory.evidence_chain_models import EvidenceBundle


EVIDENCE_RECALL_MARKERS = (
    "based on what",
    "based on which",
    "why did we choose",
    "why did we decide",
    "why did we use",
    "why did i choose",
    "why did i decide",
    "what is this based on",
    "where did this come from",
    "which message",
    "which messages",
    "show the source",
    "show source",
    "show the evidence",
    "what supports this",
    "how exactly did i say",
    "exact wording",
    "почему мы выбрали",
    "почему мы решили",
    "почему я выбрал",
    "почему я выбрала",
    "на основании чего",
    "на чем основано",
    "на чём основано",
    "из какого сообщения",
    "из каких сообщений",
    "какие сообщения",
    "покажи источник",
    "покажи доказательства",
    "как именно я сказал",
    "дословно",
    "tuo paremta",
    "kuo tai pagrįsta",
    "iš kurios žinutės",
    "iš kokių žinučių",
    "parodyk šaltinį",
    "parodyk įrodymus",
    "kaip tiksliai pasakiau",
    "kodėl pasirinkome",
    "kodėl nusprendėme",
)
MAX_EVIDENCE_RECORDS = 3
MAX_SOURCE_MESSAGES = 6


def _clean_text(value: object, default: str = "") -> str:
    """Return one string value stripped of whitespace."""
    return str(value if value is not None else default).strip()


def _summary_text(bundle: EvidenceBundle) -> str:
    """Return the main summary line for one evidence bundle."""
    return _clean_text(bundle.summary.get("main_point", "")) or "None."


def _record_summaries(bundle: EvidenceBundle) -> list[str]:
    """Return non-empty record summaries limited to the configured count."""
    return [
        summary
        for summary in (
            _clean_text(record.get("summary", ""))
            for record in bundle.records[:MAX_EVIDENCE_RECORDS]
        )
        if summary
    ]


def _source_messages(bundle: EvidenceBundle) -> list[str]:
    """Return formatted source messages limited to the configured count."""
    return [
        _format_source_message(message)
        for message in bundle.source_messages[:MAX_SOURCE_MESSAGES]
        if _clean_text(message.get("content", ""))
    ]


def is_evidence_recall_question(message: str) -> bool:
    """Return True when one question explicitly asks for memory support."""
    normalized_message = message.casefold()
    return any(marker in normalized_message for marker in EVIDENCE_RECALL_MARKERS)


def _format_source_message(message: dict[str, object]) -> str:
    """Render one source message with role and optional timestamp."""
    role = _clean_text(message.get("role", "unknown"), "unknown")
    content = _clean_text(message.get("content", ""))
    created_at = _clean_text(message.get("created_at", ""))
    if created_at:
        return f"{created_at} {role}: {content}".strip()
    return f"{role}: {content}".strip()


def _append_context_section(
    lines: list[str],
    *,
    title: str,
    items: list[str],
) -> None:
    """Append one titled bullet section when it has visible items."""
    if not items:
        return
    lines.append(title)
    lines.extend(f"- {item}" for item in items)


def build_evidence_recall_context(bundle: EvidenceBundle) -> str:
    """Render one evidence bundle into a compact system context block."""
    summary_main_point = _summary_text(bundle)
    records = _record_summaries(bundle)
    source_messages = _source_messages(bundle)

    lines = [
        "Evidence recall support:",
        "- The summary is a higher-level memory interpretation.",
        "- Records summarize source messages.",
        "- Source messages are the strongest evidence.",
        (
            "- If details conflict, trust source messages over records, "
            "and records over the summary."
        ),
        "- If exact wording is unavailable, say that plainly instead of guessing.",
        "",
        f"Summary: {summary_main_point}",
    ]
    _append_context_section(lines, title="Records:", items=records)
    _append_context_section(
        lines,
        title="Source messages:",
        items=source_messages,
    )
    return "\n".join(lines)
