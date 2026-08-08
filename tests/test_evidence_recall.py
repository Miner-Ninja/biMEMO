"""Tests for narrow evidence-backed recall helpers."""

from backend.memory.evidence_chain_models import EvidenceBundle
from backend.memory.evidence_recall import (
    build_evidence_recall_context,
    is_evidence_recall_question,
)


def test_is_evidence_recall_question_detects_explicit_source_request() -> None:
    """Questions that ask for source proof should trigger evidence recall."""
    assert is_evidence_recall_question(
        "What is this based on and which messages support it?",
    )


def test_is_evidence_recall_question_ignores_normal_memory_recall() -> None:
    """Plain memory recall without a source request should stay on the normal path."""
    assert not is_evidence_recall_question("What do you remember about me?")


def test_is_evidence_recall_question_detects_why_decision_questions() -> None:
    """Why-decision questions should also trigger the deep evidence path."""
    assert is_evidence_recall_question("Why did we choose CSV for exports?")


def test_build_evidence_recall_context_formats_summary_records_and_messages() -> None:
    """Evidence context should explain source priority and show linked support."""
    bundle = EvidenceBundle(
        summary={"main_point": "User prefers detailed debugging answers."},
        records=[
            {"summary": "The user asked for step-by-step debugging help."},
        ],
        source_messages=[
            {
                "role": "user",
                "content": "Please explain debugging issues step by step.",
                "created_at": "2026-03-24T11:22:33Z",
            }
        ],
    )

    context_block = build_evidence_recall_context(bundle)

    assert "Evidence recall support:" in context_block
    assert "Summary: User prefers detailed debugging answers." in context_block
    assert "Records:" in context_block
    assert "Source messages:" in context_block
    assert (
        "2026-03-24T11:22:33Z user: Please explain debugging issues step by step."
        in context_block
    )
