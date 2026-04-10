"""Tests for canonical temporary-context signal naming."""

from backend.memory.time_signals import TimeSignals


def test_time_signal_dictionary_uses_snake_case_keys() -> None:
    """Canonical time-signal keys should use one stable snake_case style."""
    assert "short_term" in TimeSignals.ALIASES
    assert "session_only" in TimeSignals.ALIASES
    assert "validation_run" in TimeSignals.ALIASES
    assert "must_restate_later" in TimeSignals.ALIASES
    assert "future_answers" in TimeSignals.ALIASES


def test_signal_aliases_keep_space_and_hyphen_variants() -> None:
    """One canonical key should still recognize natural text variants."""
    aliases = TimeSignals.get_aliases("short_term")
    assert "short term" in aliases
    assert "short-term" in aliases
    assert "short_term" in aliases


def test_phrase_set_flattens_multiple_signal_groups() -> None:
    """Signal groups should expand into one normalized phrase set."""
    phrases = TimeSignals.build_phrase_set(
        TimeSignals.TEMPORARY_CONTEXT_KEYS + TimeSignals.NON_PERSISTENT_KEYS
    )
    assert "temporary" in phrases
    assert "validation run" in phrases
    assert "must restate later" in phrases
    assert "won't persist" in phrases


def test_text_contains_one_specific_time_signal() -> None:
    """One canonical key should match any configured text alias."""
    assert TimeSignals.text_contains_signal(
        "This preference is short-term for the current run.",
        "short_term",
    )
    assert TimeSignals.text_contains_signal(
        "You must restate later if you want this again.",
        "must_restate_later",
    )


def test_text_contains_any_signal_across_group_boundaries() -> None:
    """Grouped checks should detect both temporary and durable markers."""
    assert TimeSignals.text_contains_any(
        "This is only for the validation run and it will not persist.",
        TimeSignals.TEMPORARY_CONTEXT_KEYS + TimeSignals.NON_PERSISTENT_KEYS,
    )
    assert TimeSignals.text_contains_any(
        "This should guide future answers as the active preference.",
        TimeSignals.DURABLE_PREFERENCE_KEYS,
    )
