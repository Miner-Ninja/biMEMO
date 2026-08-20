"""OpenRouter chat client adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from backend.constants import ENV_OPENROUTER_NUM_CTX
from backend.llm.openai_compatible.client import (
    DEFAULT_OPENAI_API_PREFIX as DEFAULT_OPENROUTER_API_PREFIX,
)
from backend.llm.openai_compatible.client import (
    DEFAULT_OPENAI_COMPAT_CONTEXT_TOKENS,
    EMPTY_LLM_RESPONSE_WARNING,
    SOURCE_ENV_OVERRIDE,
    SOURCE_PROVIDER_DEFAULT,
    SOURCE_UI_OVERRIDE,
    OpenAICompatibleClient,
    OpenAICompatibleClientError,
    _build_auth_headers,
    normalize_system_messages,
)


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
    "DEFAULT_OPENAI_COMPAT_CONTEXT_TOKENS",
    "DEFAULT_OPENROUTER_API_PREFIX",
    "EMPTY_LLM_RESPONSE_WARNING",
    "SOURCE_ENV_OVERRIDE",
    "SOURCE_OPENROUTER_MODELS",
    "SOURCE_PROVIDER_DEFAULT",
    "SOURCE_UI_OVERRIDE",
    "OpenRouterClient",
    "OpenRouterClientError",
    "_build_auth_headers",
    "normalize_system_messages",
]
