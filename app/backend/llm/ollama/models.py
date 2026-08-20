"""Data models and exceptions for the Ollama LLM provider.

Defines ChatMessage and ChatResponse dataclasses used across
LLM providers, and Ollama-specific exception hierarchy.
Separated from provider.py to allow importing types
without loading the full provider with its httpx dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class OllamaError(Exception):
    """Raised when Ollama communication fails."""


class ModelNotAvailableError(OllamaError):
    """Raised when a required model is not available."""


class OllamaClientError(Exception):
    """Raised when Ollama client operations fail."""


@dataclass
class ChatMessage:
    """A single message in a chat conversation.

    Attributes:
        role: Message role (system, user, assistant).
        content: Message text content.
    """

    role: str
    content: str | list[dict[str, Any]]


@dataclass
class ChatResponse:
    """Response from a chat completion request.

    Attributes:
        content: Generated text content.
        model: Model that generated the response.
        done: Whether generation is complete.
    """

    content: str
    model: str
    done: bool
