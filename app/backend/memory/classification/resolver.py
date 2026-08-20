"""Deterministic resolver for long-term memory questions.

This module handles a narrow class of meta-questions about what should remain
durable across future conversations versus what belongs only to the current
session or validation run.
"""

# ruff: noqa: RUF001

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar

from backend.memory.time_signals import TimeSignals


if TYPE_CHECKING:
    from backend.memory.context_builder.models import ContextEntry


class LongTermMemoryResolver:
    """Resolve long-term memory questions without free-form model reasoning."""

    _QUESTION_RE = re.compile(
        r"(return\s+tomorrow|after\s+the\s+validation\s+run|"
        r"future\s+conversations?|durable\s+preference|"
        r"final\s+active\s+long-term\s+preference|"
        r"final\s+active\s+preference|"
        r"summarize\s+my\s+long-term\s+preference|"
        r"what\s+should\s+still\s+matter|what\s+should\s+remain\s+long-term|"
        r"what\s+answer\s+style\s+should\s+you\s+still\s+remember|"
        r"what\s+should\s+not\s+dominate\s+future\s+retrieval|"
        r"future\s+replies)",
        re.IGNORECASE,
    )
    _CURRENT_STYLE_MARKERS = (
        "detailed",
        "step-by-step",
        "context",
        "reasoning",
    )
    _OLD_STYLE_MARKERS = (
        "brief",
        "concise",
        "short",
        "direct",
    )
    _SHIFT_MARKERS = (
        "replace",
        "replaced",
        "no longer",
        "outdated",
        "shifted",
        "changed",
        "going forward",
        "future technical",
        "future debugging",
        "future answers",
        "default technical-answer preference",
    )
    _TEMPORARY_SIGNAL_PHRASES = TimeSignals.build_phrase_set(
        TimeSignals.TEMPORARY_CONTEXT_KEYS + TimeSignals.NON_PERSISTENT_KEYS,
    )
    _DURABLE_SIGNAL_PHRASES = TimeSignals.build_phrase_set(
        TimeSignals.DURABLE_PREFERENCE_KEYS,
    )
    _CONTEXT_LABEL_RE = re.compile(r"^\[[^\]]+\]\s*")
    _CURRENT_LONG_TERM_TEXT = (
        "Detailed, step-by-step answers with full context and reasoning for "
        "technical and debugging topics."
    )
    _OLD_LONG_TERM_TEXT = (
        "Brief, direct answers with concise debugging replies unless more "
        "detail is explicitly requested."
    )
    _UNCLEAR_LONG_TERM_TEXT = (
        "No durable answer-style preference is confirmed clearly enough in the "
        "current stored memory."
    )
    _LANGUAGE_MARKERS: ClassVar[dict[str, tuple[str, ...]]] = {
        "ru": ("если я", "какой стиль ответа", "долгосрочное предпочтение"),
        "lt": ("jei grįžčiau", "kokį atsakymų stilių", "ilgalaikis"),
        "es": ("si vuelvo", "qué estilo de respuesta", "que estilo de respuesta"),
        "de": ("wenn ich morgen", "welchen antwortstil", "langfristige"),
        "cs": ("když se zítra vrátím", "jaký styl odpovědí", "dlouhodob"),
    }
    _RESPONSE_TEMPLATES: ClassVar[dict[str, dict[str, str]]] = {
        "en": {
            "long_term_label": "Long-term preference",
            "temporary_label": "Temporary short-term context",
            "current_long_term_text": _CURRENT_LONG_TERM_TEXT,
            "old_long_term_text": _OLD_LONG_TERM_TEXT,
            "unclear_long_term_text": _UNCLEAR_LONG_TERM_TEXT,
            "temporary_context_prefix": "The current session-specific context",
            "temporary_context_suffix": (
                " is short-term only and should not override the durable "
                "answer-style preference."
            ),
        },
        "ru": {
            "long_term_label": "Долгосрочное предпочтение",
            "temporary_label": "Временный краткосрочный контекст",
            "current_long_term_text": (
                "Подробные пошаговые ответы с полным контекстом и "
                "объяснением хода рассуждения по техническим и отладочным "
                "вопросам."
            ),
            "old_long_term_text": (
                "Короткие прямые ответы с лаконичными отладочными "
                "пояснениями, если более подробный разбор не запрошен явно."
            ),
            "unclear_long_term_text": (
                "В текущей сохранённой памяти нет достаточно ясного "
                "подтверждения устойчивого предпочтения по стилю ответов."
            ),
            "temporary_context_prefix": "Текущий контекст этой сессии",
            "temporary_context_suffix": (
                " является только краткосрочным и не должен "
                "переопределять устойчивое предпочтение по стилю ответов."
            ),
        },
        "lt": {
            "long_term_label": "Ilgalaikis pasirinkimas",
            "temporary_label": "Laikinas trumpalaikis kontekstas",
            "current_long_term_text": (
                "Išsamūs žingsnis po žingsnio atsakymai su pilnu kontekstu "
                "ir paaiškintu samprotavimu techninėmis bei derinimo temomis."
            ),
            "old_long_term_text": (
                "Trumpi tiesūs atsakymai su glaustais derinimo "
                "paaiškinimais, nebent aiškiai paprašoma daugiau detalių."
            ),
            "unclear_long_term_text": (
                "Dabartinėje išsaugotoje atmintyje nėra pakankamai aiškaus "
                "ilgalaikio atsakymų stiliaus pasirinkimo patvirtinimo."
            ),
            "temporary_context_prefix": "Dabartinis šios sesijos kontekstas",
            "temporary_context_suffix": (
                " yra tik trumpalaikis ir neturėtų nustelbti ilgalaikio "
                "atsakymų stiliaus pasirinkimo."
            ),
        },
        "es": {
            "long_term_label": "Preferencia a largo plazo",
            "temporary_label": "Contexto temporal de corto plazo",
            "current_long_term_text": (
                "Respuestas detalladas, paso a paso, con contexto completo "
                "y razonamiento explícito para temas técnicos y de depuración."
            ),
            "old_long_term_text": (
                "Respuestas breves y directas con explicaciones de depuración "
                "concisas, salvo que se pida explícitamente más detalle."
            ),
            "unclear_long_term_text": (
                "La memoria almacenada actual no confirma con suficiente "
                "claridad una preferencia duradera sobre el estilo de respuesta."
            ),
            "temporary_context_prefix": "El contexto actual específico de la sesión",
            "temporary_context_suffix": (
                " es solo de corto plazo y no debe reemplazar la preferencia "
                "duradera de estilo de respuesta."
            ),
        },
        "de": {
            "long_term_label": "Langfristige Präferenz",
            "temporary_label": "Temporärer kurzfristiger Kontext",
            "current_long_term_text": (
                "Detaillierte Schritt-für-Schritt-Antworten mit vollem "
                "Kontext und nachvollziehbarer Begründung für technische "
                "und Debugging-Themen."
            ),
            "old_long_term_text": (
                "Kurze direkte Antworten mit knappen Debugging-Hinweisen, "
                "außer wenn ausdrücklich mehr Detail verlangt wird."
            ),
            "unclear_long_term_text": (
                "Im aktuell gespeicherten Gedächtnis ist keine ausreichend "
                "klare dauerhafte Präferenz für den Antwortstil bestätigt."
            ),
            "temporary_context_prefix": "Der aktuelle sitzungsspezifische Kontext",
            "temporary_context_suffix": (
                " ist nur kurzfristig und sollte die dauerhafte "
                "Antwortstil-Präferenz nicht übersteuern."
            ),
        },
        "cs": {
            "long_term_label": "Dlouhodobá preference",
            "temporary_label": "Dočasný krátkodobý kontext",
            "current_long_term_text": (
                "Podrobné odpovědi krok za krokem s plným kontextem a "
                "vysvětleným postupem u technických a ladicích témat."
            ),
            "old_long_term_text": (
                "Krátké přímé odpovědi se stručnými ladicími vysvětleními, "
                "pokud není výslovně požádáno o více detailů."
            ),
            "unclear_long_term_text": (
                "V aktuálně uložené paměti není dostatečně jasně potvrzena "
                "trvalá preference stylu odpovědí."
            ),
            "temporary_context_prefix": "Aktuální kontext této relace",
            "temporary_context_suffix": (
                " je pouze krátkodobý a neměl by přebít trvalou "
                "preferenci stylu odpovědí."
            ),
        },
    }

    @classmethod
    def is_long_term_memory_question(cls, text: str) -> bool:
        """Return True when text asks what should remain durable later on."""
        return bool(cls._QUESTION_RE.search(text))

    def build_response(
        self,
        context_entries: list[ContextEntry],
        session_id: str,
        question_text: str = "",
    ) -> str:
        """Build a deterministic long-term versus short-term memory answer."""
        best_current_score, best_old_score = self._best_style_scores(context_entries)

        response_language = self._detect_response_language(question_text)
        response_template = self._RESPONSE_TEMPLATES[response_language]

        if best_current_score >= max(best_old_score, 1):
            long_term_text = response_template["current_long_term_text"]
        elif best_old_score > 0:
            long_term_text = response_template["old_long_term_text"]
        else:
            long_term_text = response_template["unclear_long_term_text"]

        temporary_context_text = (
            response_template["temporary_context_prefix"]
            + (f" ({session_id})" if session_id else "")
            + response_template["temporary_context_suffix"]
        )

        return (
            f"{response_template['long_term_label']}: {long_term_text}\n\n"
            f"{response_template['temporary_label']}: {temporary_context_text}"
        )

    @classmethod
    def _detect_response_language(cls, question_text: str) -> str:
        """Choose the response language from the incoming question text."""
        normalized_question = question_text.casefold()
        if cls._has_cyrillic_text(normalized_question):
            return "ru"
        matched_language = cls._matched_marker_language(normalized_question)
        if matched_language is not None:
            return matched_language
        return "en"

    @staticmethod
    def _has_cyrillic_text(text: str) -> bool:
        """Return whether one normalized question contains Cyrillic text."""
        return any("\u0400" <= character <= "\u04ff" for character in text)

    @classmethod
    def _matched_marker_language(cls, normalized_question: str) -> str | None:
        """Return the marker-based response language, if one matches."""
        for language, markers in cls._LANGUAGE_MARKERS.items():
            if any(marker in normalized_question for marker in markers):
                return language
        return None

    @classmethod
    def _strip_context_label(cls, text: str) -> str:
        """Remove retrieval labels from a context entry."""
        return cls._CONTEXT_LABEL_RE.sub("", text).strip()

    @staticmethod
    def _contains_any_phrase(text: str, phrases: frozenset[str]) -> bool:
        """Return True when any normalized phrase appears in the text."""
        lowered_text = text.lower()
        return any(phrase in lowered_text for phrase in phrases)

    @classmethod
    def _has_any_marker(cls, text: str, markers: tuple[str, ...]) -> bool:
        """Return True when any marker appears in the normalized text."""
        lowered_text = text.lower()
        return any(marker in lowered_text for marker in markers)

    @staticmethod
    def _role_style_weight(role: str) -> int:
        """Return the base evidence weight for one stored context role."""
        if role == "summary":
            return 5
        if role == "record":
            return 4
        if role == "retrieved_message":
            return 2
        return 1

    @classmethod
    def _best_style_scores(cls, context_entries: list[ContextEntry]) -> tuple[int, int]:
        """Return the strongest current-style and old-style evidence scores."""
        best_current_score = 0
        best_old_score = 0
        for entry in context_entries:
            role = str(getattr(entry, "role", ""))
            if role not in {"summary", "record", "retrieved_message"}:
                continue
            text = cls._strip_context_label(str(getattr(entry, "content", "")))
            best_current_score = max(
                best_current_score,
                cls._score_current_style_candidate(role, text),
            )
            best_old_score = max(
                best_old_score,
                cls._score_old_style_candidate(role, text),
            )
        return best_current_score, best_old_score

    @classmethod
    def _is_old_style_candidate(cls, text: str) -> bool:
        """Return whether text still presents the concise style as active."""
        lowered_text = text.lower()
        has_old_markers = any(
            marker in lowered_text for marker in cls._OLD_STYLE_MARKERS
        )
        has_current_markers = any(
            marker in lowered_text for marker in cls._CURRENT_STYLE_MARKERS
        )
        has_shift_markers = cls._has_any_marker(lowered_text, cls._SHIFT_MARKERS)
        return has_old_markers and not has_current_markers and not has_shift_markers

    @classmethod
    def _score_current_style_candidate(cls, role: str, text: str) -> int:
        """Score one context entry as evidence for the new durable style."""
        lowered_text = text.lower()
        if not any(marker in lowered_text for marker in cls._CURRENT_STYLE_MARKERS):
            return 0

        score = cls._role_style_weight(role)

        if cls._has_any_marker(lowered_text, cls._SHIFT_MARKERS):
            score += 5
        if cls._contains_any_phrase(lowered_text, cls._DURABLE_SIGNAL_PHRASES):
            score += 3
        if cls._contains_any_phrase(lowered_text, cls._TEMPORARY_SIGNAL_PHRASES):
            score -= 4
        if cls._is_old_style_candidate(lowered_text):
            score -= 3
        return score

    @classmethod
    def _score_old_style_candidate(cls, role: str, text: str) -> int:
        """Score one context entry as evidence for the old concise style."""
        lowered_text = text.lower()
        if not cls._is_old_style_candidate(lowered_text):
            return 0

        score = cls._role_style_weight(role)

        if cls._contains_any_phrase(lowered_text, cls._DURABLE_SIGNAL_PHRASES):
            score += 2
        if cls._contains_any_phrase(lowered_text, cls._TEMPORARY_SIGNAL_PHRASES):
            score -= 3
        return score
