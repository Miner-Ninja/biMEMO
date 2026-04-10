"""In-process registry caching resolved model capabilities.

Holds the ``(provider_name, model) -> ModelCapabilities`` cache for the
lifetime of the orchestrator process. Resolution is performed lazily on
first request via the supplied provider's ``resolve_capabilities`` method,
which is responsible for running the override-or-detect chain. The cache
is process-local, never persisted, and never shared across processes.
Concurrent requests for the same key are serialized through an
``asyncio.Lock`` so warmup loads are not duplicated.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol


if TYPE_CHECKING:
    from backend.llm.capabilities import ModelCapabilities


class CapabilityProvider(Protocol):
    """Minimal provider surface required by the capabilities registry.

    Concrete LLM providers (Ollama, OpenRouter, etc.) implement this
    protocol implicitly by exposing a ``name`` attribute and an async
    ``resolve_capabilities`` method.
    """

    name: str

    async def resolve_capabilities(self, model: str) -> ModelCapabilities:
        """Run the override-or-detect chain and return capabilities."""


class CapabilitiesRegistry:
    """Process-local cache of resolved model capabilities.

    Attributes:
        _cache: Mapping from ``(provider_name, model)`` to the resolved
            ``ModelCapabilities`` object.
        _lock: Async lock that serializes resolution attempts so the same
            ``(provider_name, model)`` pair is never resolved twice
            concurrently.
    """

    def __init__(self) -> None:
        """Construct an empty registry with a fresh asyncio lock."""
        self._cache: dict[tuple[str, str], ModelCapabilities] = {}
        self._lock = asyncio.Lock()

    async def get_or_detect(
        self,
        provider: CapabilityProvider,
        model: str,
    ) -> ModelCapabilities:
        """Return cached capabilities or run the resolver under the lock.

        Args:
            provider: Concrete LLM provider exposing ``name`` and
                ``resolve_capabilities``.
            model: Provider-specific model identifier.

        Returns:
            The cached or freshly-resolved ``ModelCapabilities``.

        Raises:
            ContextDetectionError: If the provider's resolver fails. The
                error is propagated unchanged; nothing is cached on
                failure, so subsequent calls retry resolution.
        """
        key = (provider.name, model)
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            resolved = await provider.resolve_capabilities(model)
            self._cache[key] = resolved
            return resolved

    def clear(self) -> None:
        """Drop all cached capabilities. Intended for tests and diagnostics."""
        self._cache.clear()

    def snapshot(self) -> dict[tuple[str, str], ModelCapabilities]:
        """Return a shallow copy of the current cache, for diagnostics."""
        return dict(self._cache)
