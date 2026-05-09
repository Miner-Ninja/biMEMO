"""Custom LLM Provider adapter for background LLM jobs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from backend.llm.openai_compatible.client import CUSTOM_API_PREFIX
from backend.llm.openai_compatible.provider import (
    OpenAICompatibleProvider,
    OpenAICompatibleProviderError,
)


@dataclass
class CustomLLMProvider(OpenAICompatibleProvider):
    """Adapter for user-provided OpenAI-compatible background LLM endpoints."""

    provider_label: ClassVar[str] = "Custom LLM Provider"
    api_prefix: str = CUSTOM_API_PREFIX


CustomLLMProviderError = OpenAICompatibleProviderError


__all__ = [
    "CUSTOM_API_PREFIX",
    "CustomLLMProvider",
    "CustomLLMProviderError",
]
