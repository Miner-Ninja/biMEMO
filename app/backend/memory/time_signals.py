"""Canonical time-signal vocabulary for memory processing."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar


if TYPE_CHECKING:
    from collections.abc import Iterable


class TimeSignals:
    """Provide canonical signal groups and lookup helpers."""

    ALIASES: ClassVar[dict[str, tuple[str, ...]]] = {
        "temporary": ("temporary",),
        "short_term": ("short term", "short-term", "short_term"),
        "session_only": ("session only", "session-only", "session_only"),
        "session_bound": (
            "for this session",
            "within this session",
            "only in this session",
            "for the current session",
            "within the current session",
            "active session",
        ),
        "session_specific": (
            "session specific",
            "session-specific",
            "session_specific",
        ),
        "current_session": ("current session", "current_session"),
        "current_run": ("current run", "current-run", "current_run"),
        "validation_run": ("validation run", "validation-run", "validation_run"),
        "test_run": ("test run", "test-run", "test_run"),
        "temporary_context": (
            "temporary context",
            "temporary-context",
            "temporary_context",
        ),
        "not_persistent": (
            "not persistent",
            "won't persist",
            "will not persist",
            "does not persist",
        ),
        "not_durable": ("not durable", "not-durable", "not_durable"),
        "not_permanent": ("not permanent", "not-permanent", "not_permanent"),
        "must_restate_later": (
            "must restate later",
            "need to restate later",
        ),
        "must_remind_again": (
            "must remind again",
            "need to remind again",
            "must be specified again",
            "need to say this again next time",
        ),
        "future_answers": ("future answers", "future-answers", "future_answers"),
        "future_conversations": (
            "future conversations",
            "future-conversations",
            "future_conversations",
        ),
        "active_preference": (
            "active preference",
            "active-preference",
            "active_preference",
        ),
        "default_preference": (
            "default preference",
            "default-preference",
            "default_preference",
        ),
        "long_term": ("long term", "long-term", "long_term"),
        "durable": ("durable",),
    }

    TEMPORARY_CONTEXT_KEYS: ClassVar[tuple[str, ...]] = (
        "temporary",
        "short_term",
        "session_only",
        "session_bound",
        "session_specific",
        "current_session",
        "current_run",
        "validation_run",
        "test_run",
        "temporary_context",
    )

    NON_PERSISTENT_KEYS: ClassVar[tuple[str, ...]] = (
        "not_persistent",
        "not_durable",
        "not_permanent",
        "must_restate_later",
        "must_remind_again",
    )

    DURABLE_PREFERENCE_KEYS: ClassVar[tuple[str, ...]] = (
        "future_answers",
        "future_conversations",
        "active_preference",
        "default_preference",
        "long_term",
        "durable",
    )

    @classmethod
    def get_aliases(cls, signal_key: str) -> tuple[str, ...]:
        """Return text aliases for one canonical signal key."""
        return cls.ALIASES.get(signal_key, ())

    @classmethod
    def _normalized_aliases(cls, signal_key: str) -> tuple[str, ...]:
        """Return case-folded aliases for one canonical signal key."""
        return tuple(alias.casefold() for alias in cls.get_aliases(signal_key))

    @classmethod
    def _normalized_text(cls, text: str) -> str:
        """Return one case-folded text string for signal matching."""
        return text.casefold()

    @classmethod
    def build_phrase_set(cls, signal_keys: Iterable[str]) -> frozenset[str]:
        """Flatten canonical signal keys into one normalized phrase set."""
        phrases: set[str] = set()
        for signal_key in signal_keys:
            phrases.update(cls._normalized_aliases(signal_key))
        return frozenset(phrases)

    @classmethod
    def text_contains_signal(cls, text: str, signal_key: str) -> bool:
        """Return True when text contains any alias of the canonical signal."""
        normalized_text = cls._normalized_text(text)
        return any(
            alias in normalized_text for alias in cls._normalized_aliases(signal_key)
        )

    @classmethod
    def text_contains_any(cls, text: str, signal_keys: Iterable[str]) -> bool:
        """Return True when text contains any alias from the given signal keys."""
        normalized_text = cls._normalized_text(text)
        return any(
            alias in normalized_text for alias in cls.build_phrase_set(signal_keys)
        )
