"""Matrix tests for equivalent memory-question intents across languages."""

import pytest
from backend.memory.classification.models import (
    ClassificationConfidence,
    MemoryQuestionIntent,
)
from backend.memory.classification.question_router import route_memory_question


@pytest.mark.parametrize(
    ("question", "intent", "role_filter", "confidence"),
    [
        (
            "What did you tell me about Ollama earlier?",
            MemoryQuestionIntent.ASSISTANT_RECALL_QUESTION,
            "assistant",
            ClassificationConfidence.HIGH,
        ),
        (
            "Ты мне говорил про настройки Ollama?",
            MemoryQuestionIntent.ASSISTANT_RECALL_QUESTION,
            "assistant",
            ClassificationConfidence.HIGH,
        ),
        (
            "Tu man sakei apie Ollama nustatymus?",
            MemoryQuestionIntent.ASSISTANT_RECALL_QUESTION,
            "assistant",
            ClassificationConfidence.HIGH,
        ),
        (
            "Qué me dijiste sobre Ollama?",
            MemoryQuestionIntent.ASSISTANT_RECALL_QUESTION,
            "assistant",
            ClassificationConfidence.HIGH,
        ),
        (
            "Was hast du mir über Ollama gesagt?",
            MemoryQuestionIntent.ASSISTANT_RECALL_QUESTION,
            "assistant",
            ClassificationConfidence.HIGH,
        ),
        (
            "Co jsi mi říkal o Ollama?",
            MemoryQuestionIntent.ASSISTANT_RECALL_QUESTION,
            "assistant",
            ClassificationConfidence.HIGH,
        ),
    ],
)
def test_assistant_recall_intent_stays_equivalent_across_supported_languages(
    question: str,
    intent: MemoryQuestionIntent,
    role_filter: str,
    confidence: ClassificationConfidence,
):
    """Equivalent assistant-recall prompts should keep one shared route."""
    route = route_memory_question(question)

    assert route.intent is intent
    assert route.role_filter == role_filter
    assert route.memory_query is True
    assert route.long_term_memory_question is False
    assert route.confidence is confidence
    assert route.source == "signal"


@pytest.mark.parametrize(
    "question",
    [
        "If I return tomorrow, what answer style should you still remember?",
        "Если я вернусь завтра, какой стиль ответа ты должен помнить?",
        "Jei grįžčiau rytoj, kokį atsakymų stilių turėtum prisiminti?",
    ],
)
def test_long_term_intent_stays_equivalent_across_supported_languages(
    question: str,
):
    """Equivalent long-term prompts should keep one shared route."""
    route = route_memory_question(question)

    assert route.intent is MemoryQuestionIntent.LONG_TERM_MEMORY_QUESTION
    assert route.role_filter is None
    assert route.memory_query is True
    assert route.long_term_memory_question is True
    assert route.confidence is ClassificationConfidence.HIGH
    assert route.source == "signal"


@pytest.mark.parametrize(
    ("question", "intent", "role_filter", "confidence"),
    [
        (
            "What did I tell you about my timezone earlier?",
            MemoryQuestionIntent.USER_RECALL_QUESTION,
            "user",
            ClassificationConfidence.HIGH,
        ),
        (
            "Я тебе говорил про мой часовой пояс?",
            MemoryQuestionIntent.USER_RECALL_QUESTION,
            "user",
            ClassificationConfidence.HIGH,
        ),
        (
            "Aš tau sakiau apie savo laiko juostą?",
            MemoryQuestionIntent.USER_RECALL_QUESTION,
            "user",
            ClassificationConfidence.HIGH,
        ),
        (
            "Qué te dije sobre mi zona horaria?",
            MemoryQuestionIntent.USER_RECALL_QUESTION,
            "user",
            ClassificationConfidence.HIGH,
        ),
        (
            "Was habe ich dir über meine Zeitzone gesagt?",
            MemoryQuestionIntent.USER_RECALL_QUESTION,
            "user",
            ClassificationConfidence.HIGH,
        ),
        (
            "Co jsem ti říkal o svém časovém pásmu?",
            MemoryQuestionIntent.USER_RECALL_QUESTION,
            "user",
            ClassificationConfidence.HIGH,
        ),
    ],
)
def test_user_recall_intent_stays_equivalent_across_supported_languages(
    question: str,
    intent: MemoryQuestionIntent,
    role_filter: str,
    confidence: ClassificationConfidence,
):
    """Equivalent user-recall prompts should keep one shared route."""
    route = route_memory_question(question)

    assert route.intent is intent
    assert route.role_filter == role_filter
    assert route.memory_query is True
    assert route.long_term_memory_question is False
    assert route.confidence is confidence
    assert route.source == "signal"


@pytest.mark.parametrize(
    "question",
    [
        "What were we talking about earlier?",
        "Что мы обсуждали раньше?",
        "Apie ką kalbėjome anksčiau?",
        "Qué recuerdas de nuestra discusión sobre Ollama?",
        "Was erinnerst du aus unserer Diskussion über Ollama?",
        "Co si pamatuješ z naší diskuse o Ollama?",
    ],
)
def test_general_memory_recall_stays_equivalent_across_supported_languages(
    question: str,
):
    """Equivalent generic recall prompts should keep one shared route."""
    route = route_memory_question(question)

    assert route.intent is MemoryQuestionIntent.MEMORY_RECALL_QUESTION
    assert route.role_filter is None
    assert route.memory_query is True
    assert route.long_term_memory_question is False
    assert route.confidence is ClassificationConfidence.MEDIUM
    assert route.fallback_recommended is False
    assert route.source == "signal"


@pytest.mark.parametrize(
    "question",
    [
        "Is this only temporary context or an active preference?",
        "Это только временный контекст или активное предпочтение?",
        "Ar tai tik laikinas kontekstas, ar aktyvus pasirinkimas?",
    ],
)
def test_ambiguous_temporary_versus_durable_questions_stay_low_confidence(
    question: str,
):
    """Equivalent ambiguous prompts should request fallback consistently."""
    route = route_memory_question(question)

    assert route.intent is MemoryQuestionIntent.MEMORY_RECALL_QUESTION
    assert route.role_filter is None
    assert route.memory_query is True
    assert route.long_term_memory_question is False
    assert route.confidence is ClassificationConfidence.LOW
    assert route.fallback_recommended is True
    assert route.source == "signal"
