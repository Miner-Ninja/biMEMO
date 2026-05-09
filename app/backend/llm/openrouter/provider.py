"""OpenRouter provider adapter for background LLM jobs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from backend.llm.openai_compatible.client import DEFAULT_OPENAI_API_PREFIX
from backend.llm.openai_compatible.provider import (
    OpenAICompatibleProvider,
    OpenAICompatibleProviderError,
)


DEFAULT_OPENROUTER_API_PREFIX = DEFAULT_OPENAI_API_PREFIX


@dataclass
class OpenRouterProvider(OpenAICompatibleProvider):
    """OpenRouter-specific adapter for background LLM jobs."""

    provider_label: ClassVar[str] = "OpenRouter provider"
    base_url: str = "https://openrouter.ai"
    api_prefix: str = DEFAULT_OPENROUTER_API_PREFIX


OpenRouterError = OpenAICompatibleProviderError


__all__ = [
    "DEFAULT_OPENROUTER_API_PREFIX",
    "OpenRouterError",
    "OpenRouterProvider",
]
