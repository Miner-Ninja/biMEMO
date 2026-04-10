"""Tests for CapabilitiesRegistry resolution and caching behavior.

Covers cold-cache resolution, warm-cache hit, concurrent serialization
for the same key, failure non-caching, and the clear/snapshot helpers.
Uses a fake provider that records call counts so the tests can assert
that resolution happens exactly the right number of times.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from backend.llm.capabilities import ContextDetectionError, ModelCapabilities
from backend.llm.capabilities_registry import CapabilitiesRegistry


# --- Fake provider used by all tests ---


class FakeProvider:
    """Records call counts and returns configurable results."""

    def __init__(
        self,
        name: str = "fake",
        max_context_tokens: int = 8192,
        delay: float = 0.0,
        raises: Exception | None = None,
    ) -> None:
        """Configure the fake provider's response and behavior."""
        self.name = name
        self._max_context_tokens = max_context_tokens
        self._delay = delay
        self._raises = raises
        self.call_count = 0

    async def resolve_capabilities(self, model: str) -> ModelCapabilities:
        """Return canned capabilities, optionally after a delay or exception."""
        self.call_count += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raises is not None:
            raise self._raises
        return ModelCapabilities(
            provider=self.name,
            model=model,
            max_context_tokens=self._max_context_tokens,
            source=f"{self.name}:test",
        )


@pytest.mark.asyncio
class TestColdAndWarmCache:
    """Cold-cache resolution and warm-cache hit behavior."""

    async def test_first_call_invokes_provider(self) -> None:
        """The first call for a key triggers resolve_capabilities."""
        provider = FakeProvider()
        registry = CapabilitiesRegistry()
        caps = await registry.get_or_detect(provider, "qwen3:8b")
        assert caps.max_context_tokens == 8192
        assert provider.call_count == 1

    async def test_second_call_hits_cache(self) -> None:
        """A repeated call for the same key returns cached value."""
        provider = FakeProvider()
        registry = CapabilitiesRegistry()
        first = await registry.get_or_detect(provider, "qwen3:8b")
        second = await registry.get_or_detect(provider, "qwen3:8b")
        assert second is first
        assert provider.call_count == 1

    async def test_different_models_resolved_independently(self) -> None:
        """Different model strings are cached under separate keys."""
        provider = FakeProvider()
        registry = CapabilitiesRegistry()
        await registry.get_or_detect(provider, "qwen3:8b")
        await registry.get_or_detect(provider, "qwen3:32b")
        assert provider.call_count == 2

    async def test_different_providers_resolved_independently(self) -> None:
        """Different provider names are cached under separate keys."""
        provider_a = FakeProvider(name="ollama")
        provider_b = FakeProvider(name="openrouter")
        registry = CapabilitiesRegistry()
        await registry.get_or_detect(provider_a, "qwen3:8b")
        await registry.get_or_detect(provider_b, "qwen3:8b")
        assert provider_a.call_count == 1
        assert provider_b.call_count == 1
        snapshot = registry.snapshot()
        assert len(snapshot) == 2


@pytest.mark.asyncio
class TestConcurrentSerialization:
    """Concurrent calls for the same key must run resolution exactly once."""

    async def test_two_concurrent_calls_same_key_run_resolver_once(self) -> None:
        """Parallel callers wait for the first resolution to complete."""
        provider = FakeProvider(delay=0.05)
        registry = CapabilitiesRegistry()
        results = await asyncio.gather(
            registry.get_or_detect(provider, "qwen3:8b"),
            registry.get_or_detect(provider, "qwen3:8b"),
            registry.get_or_detect(provider, "qwen3:8b"),
        )
        assert provider.call_count == 1
        assert all(r is results[0] for r in results)

    async def test_concurrent_calls_different_keys_both_run(self) -> None:
        """Parallel callers for different keys both trigger resolution."""
        provider = FakeProvider(delay=0.02)
        registry = CapabilitiesRegistry()
        await asyncio.gather(
            registry.get_or_detect(provider, "qwen3:8b"),
            registry.get_or_detect(provider, "qwen3:32b"),
        )
        assert provider.call_count == 2


@pytest.mark.asyncio
class TestFailureBehavior:
    """Failed resolution must not poison the cache."""

    async def test_failure_propagates(self) -> None:
        """ContextDetectionError raised by the provider is re-raised."""
        provider = FakeProvider(
            raises=ContextDetectionError(
                provider="fake",
                model="qwen3:8b",
                attempted_sources=("fake:test",),
            )
        )
        registry = CapabilitiesRegistry()
        with pytest.raises(ContextDetectionError):
            await registry.get_or_detect(provider, "qwen3:8b")

    async def test_failure_not_cached_retries_next_call(self) -> None:
        """After a failure, the next call retries the provider."""
        provider = FakeProvider(
            raises=ContextDetectionError(
                provider="fake",
                model="qwen3:8b",
            )
        )
        registry = CapabilitiesRegistry()
        with pytest.raises(ContextDetectionError):
            await registry.get_or_detect(provider, "qwen3:8b")
        with pytest.raises(ContextDetectionError):
            await registry.get_or_detect(provider, "qwen3:8b")
        assert provider.call_count == 2


@pytest.mark.asyncio
class TestRegistryHelpers:
    """clear() and snapshot() utility methods."""

    async def test_clear_drops_cache(self) -> None:
        """clear() empties the cache and forces re-resolution."""
        provider = FakeProvider()
        registry = CapabilitiesRegistry()
        await registry.get_or_detect(provider, "qwen3:8b")
        registry.clear()
        await registry.get_or_detect(provider, "qwen3:8b")
        assert provider.call_count == 2

    async def test_snapshot_returns_copy(self) -> None:
        """snapshot() returns a copy that does not reflect later writes."""
        provider = FakeProvider()
        registry = CapabilitiesRegistry()
        await registry.get_or_detect(provider, "qwen3:8b")
        snapshot = registry.snapshot()
        assert ("fake", "qwen3:8b") in snapshot
        registry.clear()
        # Snapshot is not affected by subsequent clear.
        assert ("fake", "qwen3:8b") in snapshot

    async def test_empty_registry_snapshot_is_empty(self) -> None:
        """A fresh registry yields an empty snapshot."""
        registry = CapabilitiesRegistry()
        assert registry.snapshot() == {}


@pytest.mark.asyncio
async def test_detected_at_is_monotonic_relative_to_now() -> None:
    """Sanity check: resolved caps carry a fresh detected_at timestamp."""
    before = time.monotonic()
    provider = FakeProvider()
    registry = CapabilitiesRegistry()
    caps = await registry.get_or_detect(provider, "qwen3:8b")
    after = time.monotonic()
    assert before <= caps.detected_at <= after
