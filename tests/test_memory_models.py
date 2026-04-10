"""Tests for record and summary data models.

Covers field validation, truncation, and structured output.
"""

import pytest
from backend.memory.models import (
    MAX_ITEM_LEN,
    MAX_LIST_ITEMS,
    MAX_MAIN_POINT_LEN,
    MAX_SUMMARY_LEN,
    Record,
    Summary,
    SummaryScope,
    TrustLevel,
)
from pydantic import ValidationError


class TestRecord:
    """Tests for Record model."""

    def test_valid_observation(self):
        """Verify valid record is created."""
        obs = Record(
            summary="Test summary",
            key_facts=["fact1", "fact2"],
            action_items=["item1"],
        )
        assert obs.summary == "Test summary"
        assert len(obs.key_facts) == 2
        assert len(obs.action_items) == 1

    def test_summary_truncated(self):
        """Verify summary is truncated to limit."""
        long = "x" * (MAX_SUMMARY_LEN + 100)
        obs = Record(
            summary=long,
            key_facts=[],
            action_items=[],
        )
        assert len(obs.summary) == MAX_SUMMARY_LEN

    def test_key_facts_truncated(self):
        """Verify key_facts list and items truncated."""
        facts = ["x" * (MAX_ITEM_LEN + 50)] * (MAX_LIST_ITEMS + 5)
        obs = Record(
            summary="s",
            key_facts=facts,
            action_items=[],
        )
        assert len(obs.key_facts) == MAX_LIST_ITEMS
        assert all(len(f) == MAX_ITEM_LEN for f in obs.key_facts)

    def test_action_items_truncated(self):
        """Verify action_items list and items truncated."""
        items = ["y" * (MAX_ITEM_LEN + 10)] * (MAX_LIST_ITEMS + 3)
        obs = Record(
            summary="s",
            key_facts=[],
            action_items=items,
        )
        assert len(obs.action_items) == MAX_LIST_ITEMS
        assert all(len(i) == MAX_ITEM_LEN for i in obs.action_items)

    def test_missing_field_raises(self):
        """Verify missing required field raises."""
        with pytest.raises(ValidationError):
            Record(summary="s", key_facts=[])


class TestSummary:
    """Tests for Summary model."""

    def test_valid_reflection(self):
        """Verify valid summary is created."""
        ref = Summary(
            main_point="User prefers concise answers",
            based_on=["obs1", "obs2"],
            trust_level=TrustLevel.HIGH,
        )
        assert ref.trust_level == TrustLevel.HIGH
        assert ref.scope == SummaryScope.LONG_TERM

    def test_main_point_truncated(self):
        """Verify main_point is truncated to limit."""
        long = "p" * (MAX_MAIN_POINT_LEN + 100)
        ref = Summary(
            main_point=long,
            based_on=[],
            trust_level=TrustLevel.LOW,
        )
        assert len(ref.main_point) == MAX_MAIN_POINT_LEN

    def test_based_on_truncated(self):
        """Verify based_on list and items truncated."""
        based_on = ["e" * (MAX_ITEM_LEN + 20)] * (MAX_LIST_ITEMS + 2)
        ref = Summary(
            main_point="p",
            based_on=based_on,
            trust_level=TrustLevel.MEDIUM,
        )
        assert len(ref.based_on) == MAX_LIST_ITEMS
        assert all(len(item) == MAX_ITEM_LEN for item in ref.based_on)

    def test_invalid_trust_level_raises(self):
        """Verify invalid trust level raises."""
        with pytest.raises(ValidationError):
            Summary(
                main_point="p",
                based_on=[],
                trust_level="unknown",
            )

    def test_invalid_scope_falls_back_to_long_term(self):
        """Verify unknown summary scope normalizes to long_term."""
        ref = Summary(
            main_point="p",
            based_on=[],
            trust_level=TrustLevel.MEDIUM,
            scope="unknown",
        )
        assert ref.scope == SummaryScope.LONG_TERM
