"""Multilingual signal data for memory-question classification."""

from __future__ import annotations

from typing import ClassVar


class MemoryQuestionSignals:
    """Provide canonical multilingual signal groups for routing."""

    GROUPS: ClassVar[dict[str, tuple[str, ...]]] = {
        "assistant_recall_signals": (
            "you told me",
            "you said earlier",
            "you mentioned earlier",
            "you wrote earlier",
            "did you tell me",
            "what did you tell me",
            "ты мне говорил",
            "ты находил",
            "ты мне писал",
            "ты мне рассказывал",
            "ты упоминал",
            "tu man sakei",
            "tu man rašei",
            "tu minėjai",
            "qué me dijiste",
            "que me dijiste",
            "me dijiste antes",
            "was hast du mir gesagt",
            "was hast du mir über",
            "was hast du mir ueber",
            "du hast mir gesagt",
            "co jsi mi říkal",
            "co jsi mi rekl",
        ),
        "user_recall_signals": (
            "i told you",
            "i said earlier",
            "i mentioned earlier",
            "i wrote earlier",
            "did i tell you",
            "what did i tell you",
            "я тебе говорил",
            "я тебе писал",
            "я сообщал",
            "я тебе сообщал",
            "я упоминал",
            "aš tau sakiau",
            "aš tau rašiau",
            "aš minėjau",
            "qué te dije",
            "que te dije",
            "te dije antes",
            "was habe ich dir gesagt",
            "was habe ich dir über",
            "was habe ich dir ueber",
            "ich habe dir gesagt",
            "co jsem ti říkal",
            "co jsem ti rekl",
        ),
        "general_memory_recall_signals": (
            "remember",
            "remind me",
            "what did we discuss",
            "what were we talking about",
            "what do you remember",
            "earlier you",
            "earlier i",
            "помнишь",
            "вспомни",
            "напомни",
            "что мы обсуждали",
            "что мы обсуждали раньше",
            "\u043e чем мы говорили",
            "раньше ты",
            "раньше я",
            "prisimeni",
            "primink",
            "ką mes aptarėme",
            "apie ką kalbėjome",
            "qué recuerdas",
            "que recuerdas",
            "de qué hablamos",
            "de que hablamos",
            "nuestra discusión",
            "was erinnerst du",
            "worüber haben wir gesprochen",
            "woruber haben wir gesprochen",
            "co si pamatuješ",
            "co si pamatujes",
            "o čem jsme mluvili",
            "o cem jsme mluvili",
        ),
        "long_term_memory_signals": (
            "return tomorrow",
            "future conversations",
            "future replies",
            "durable preference",
            "long-term preference",
            "what should remain long-term",
            "what should still matter",
            "what answer style should you still remember",
            "summarize my long-term preference",
            "state the final active long-term preference",
            "если я вернусь завтра",
            "в будущих разговорах",
            "в будущих ответах",
            "долгосрочное предпочтение",
            "что должно остаться в долгой памяти",
            "что должно остаться долгосрочным",
            "какой стиль ответа ты должен помнить",
            "суммируй мое долгосрочное предпочтение",
            "jei grįžčiau rytoj",
            "ateities pokalbiuose",
            "ateities atsakymuose",
            "ilgalaikis pasirinkimas",
            "kokį atsakymų stilių turėtum prisiminti",
        ),
        "temporary_context_signals": (
            "temporary context",
            "temporary task",
            "this session only",
            "session-specific",
            "validation run",
            "test run",
            "временный контекст",
            "временная задача",
            "только в этой сессии",
            "в рамках этой сессии",
            "текущий тест",
            "валидационный прогон",
            "laikinas kontekstas",
            "laikina užduotis",
            "tik šioje sesijoje",
        ),
        "durable_preference_signals": (
            "durable preference",
            "long-term preference",
            "default preference",
            "active preference",
            "future answers",
            "долгосрочное предпочтение",
            "устойчивое предпочтение",
            "активное предпочтение",
            "предпочтение по умолчанию",
            "предпочтение для будущих ответов",
            "ilgalaikis pasirinkimas",
            "aktyvus pasirinkimas",
            "numatytasis pasirinkimas",
            "ateities atsakymai",
        ),
    }

    @staticmethod
    def _normalized_text(text: str) -> str:
        """Return the case-folded form used for signal matching."""
        return text.casefold()

    @classmethod
    def _matches_normalized_group(
        cls,
        normalized_text: str,
        phrases: tuple[str, ...],
    ) -> bool:
        """Return whether normalized text matches any phrase in one signal group."""
        return any(
            cls._normalized_text(phrase) in normalized_text for phrase in phrases
        )

    @classmethod
    def match_groups(cls, text: str) -> tuple[str, ...]:
        """Return canonical signal groups that match the given text."""
        normalized_text = cls._normalized_text(text)
        matched_groups: list[str] = []
        for group_name, phrases in cls.GROUPS.items():
            if cls._matches_normalized_group(normalized_text, phrases):
                matched_groups.append(group_name)
        return tuple(matched_groups)
