"""OpenRouter chat client adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from backend.constants import ENV_OPENROUTER_NUM_CTX
from backend.llm.openai_compatible.client import OpenAICompatibleClient
from backend.llm.openai_compatible.protocol import OpenAICompatibleClientError


SOURCE_OPENROUTER_MODELS = "openrouter:/models"


@dataclass
class OpenRouterClient(OpenAICompatibleClient):
    """OpenRouter-specific adapter for the shared OpenAI-compatible client."""

    name: ClassVar[str] = "openrouter"
    env_override_names: ClassVar[tuple[str, ...]] = (ENV_OPENROUTER_NUM_CTX,)
    models_source: ClassVar[str] = SOURCE_OPENROUTER_MODELS
    base_url: str = "https://openrouter.ai"


OpenRouterClientError = OpenAICompatibleClientError


__all__ = [
    "SOURCE_OPENROUTER_MODELS",
    "OpenRouterClient",
    "OpenRouterClientError",
]
