"""Generic HTTP-based capability detector for LLM providers.

Provides one universal mechanism that any provider plugs into through a
small declarative ``HttpDetectorSpec`` dataclass. The generic detector
handles the parts shared by every HTTP-based provider: issuing the
primary request, running the spec's extractor, optionally running a
warmup callable and retrying, wrapping all errors in
``ContextDetectionError``, and assembling the diagnostic source labels.
Provider-specific knowledge (which JSON shape, which warmup logic, which
endpoint) lives entirely in per-provider spec modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from backend.llm.capabilities import ContextDetectionError


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


# --- Source-label suffixes ---
WARMUP_SUFFIX = " (after warmup)"


@dataclass(frozen=True)
class HttpDetectorSpec:
    """Per-provider description of how to detect context window via HTTP.

    Attributes:
        provider_name: Short identifier of the provider (e.g. ``"ollama"``).
            Used in the ``ContextDetectionError`` payload so callers can
            tell which provider failed.
        path: Relative URL path on the provider's HTTP base for the
            primary GET request, e.g. ``"/api/ps"`` for Ollama.
        extract: Callable that takes the decoded JSON body and the
            requested model name and returns a positive integer if the
            context window can be read from this response, or ``None`` if
            the model is not present yet (warmup may help) or the body
            is malformed.
        source_label: Human-readable identifier of where the value came
            from, written into the resulting ``ModelCapabilities.source``.
        warmup: Optional callable that forces the provider to load the
            model when the primary request did not yet expose it. Receives
            the same ``httpx.AsyncClient`` and model name. When ``None``,
            the detector raises immediately on a missing model.
    """

    provider_name: str
    path: str
    extract: Callable[[dict[str, Any], str], int | None]
    source_label: str
    warmup: Callable[[httpx.AsyncClient, str], Awaitable[None]] | None = None


async def detect_via_http(
    client: httpx.AsyncClient,
    model: str,
    spec: HttpDetectorSpec,
) -> tuple[int, str]:
    """Run the universal detection flow described by ``spec``.

    Args:
        client: Authenticated httpx client bound to the provider's base URL.
        model: Provider-specific model identifier.
        spec: The provider's declarative detector spec.

    Returns:
        A tuple ``(max_context_tokens, source_label)``. The label uses the
        spec's value, with a warmup-suffix appended when warmup was used.

    Raises:
        ContextDetectionError: If the extractor cannot produce a positive
            integer even after the optional warmup retry, or if any HTTP
            call fails.
    """
    attempted: list[str] = [spec.source_label]

    body = await _request_primary(client, spec, model, attempted)
    extracted = spec.extract(body, model)
    if extracted is not None and extracted > 0:
        return extracted, spec.source_label

    if spec.warmup is None:
        raise ContextDetectionError(
            provider=spec.provider_name,
            model=model,
            attempted_sources=tuple(attempted),
        )

    await _run_warmup(client, spec, model, attempted)

    after_warmup_label = spec.source_label + WARMUP_SUFFIX
    attempted.append(after_warmup_label)
    body = await _request_primary(client, spec, model, attempted)
    extracted = spec.extract(body, model)
    if extracted is not None and extracted > 0:
        return extracted, after_warmup_label

    raise ContextDetectionError(
        provider=spec.provider_name,
        model=model,
        attempted_sources=tuple(attempted),
    )


async def _request_primary(
    client: httpx.AsyncClient,
    spec: HttpDetectorSpec,
    model: str,
    attempted: list[str],
) -> dict[str, Any]:
    """Issue the spec's primary GET request and return its decoded JSON body."""
    try:
        response = await client.get(spec.path)
        response.raise_for_status()
    except httpx.HTTPError as cause:
        raise ContextDetectionError(
            provider=spec.provider_name,
            model=model,
            attempted_sources=tuple(attempted),
            cause=cause,
        ) from cause
    decoded = response.json()
    if not isinstance(decoded, dict):
        return {}
    return decoded


async def _run_warmup(
    client: httpx.AsyncClient,
    spec: HttpDetectorSpec,
    model: str,
    attempted: list[str],
) -> None:
    """Invoke the spec's warmup callable, wrapping HTTP errors as detection errors."""
    assert spec.warmup is not None
    try:
        await spec.warmup(client, model)
    except httpx.HTTPError as cause:
        raise ContextDetectionError(
            provider=spec.provider_name,
            model=model,
            attempted_sources=tuple(attempted),
            cause=cause,
        ) from cause
