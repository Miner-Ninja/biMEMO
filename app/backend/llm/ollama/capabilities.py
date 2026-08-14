"""Ollama runtime capability detection and KV-cache estimation.

Collects the runtime context window that Ollama actually loaded for a
model from ``/api/ps`` and supplements it with optional metadata from
``/api/show``. The runtime context window is treated as the source of
truth for request sizing because it reflects what the server could
really allocate on the current machine. ``/api/show`` is used only for
diagnostics and estimating KV-cache size.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx


# --- Ollama API endpoints ---
PS_ENDPOINT = "/api/ps"
SHOW_ENDPOINT = "/api/show"
GENERATE_ENDPOINT = "/api/generate"

# --- Detection source labels ---
SOURCE_RUNTIME = "ollama:/api/ps"
SOURCE_RUNTIME_AFTER_WARMUP = "ollama:/api/ps (after warmup)"
SOURCE_UI_OVERRIDE = "ui-override"

# --- Warmup request settings ---
WARMUP_KEEP_ALIVE = "5m"
WARMUP_PROMPT = ""
WARMUP_STREAM = False

# --- KV-cache estimation ---
KV_ELEMENT_BYTES = 2
BYTES_PER_GIB = 1024**3


class OllamaCapabilityError(Exception):
    """Raised when runtime capabilities cannot be read from Ollama."""


@dataclass(frozen=True)
class OllamaModelCapabilities:
    """Resolved Ollama runtime capabilities for one loaded model.

    Attributes:
        model: Ollama model identifier.
        runtime_context_tokens: Context window that Ollama actually
            loaded for the current runtime.
        source: Source label explaining how the runtime value was read.
        model_max_context_tokens: Optional maximum context window found
            in model metadata from ``/api/show``.
        model_size_bytes: Optional model blob size from ``/api/ps``.
        size_vram_bytes: Optional runtime VRAM usage from ``/api/ps``.
        kv_bytes_per_token: Optional KV-cache estimate per token derived
            from model metadata.
        estimated_runtime_kv_bytes: Optional estimated KV-cache size for
            ``runtime_context_tokens``.
        detected_at: Monotonic timestamp for diagnostics.
    """

    model: str
    runtime_context_tokens: int
    source: str
    model_max_context_tokens: int | None = None
    model_size_bytes: int | None = None
    size_vram_bytes: int | None = None
    kv_bytes_per_token: int | None = None
    estimated_runtime_kv_bytes: int | None = None
    detected_at: float = field(default_factory=time.monotonic)

    @property
    def max_context_tokens(self) -> int:
        """Return the runtime context size for legacy callers."""
        return self.runtime_context_tokens

    @property
    def estimated_runtime_kv_gib(self) -> float | None:
        """Return the estimated runtime KV-cache size in GiB."""
        if self.estimated_runtime_kv_bytes is None:
            return None
        return round(self.estimated_runtime_kv_bytes / BYTES_PER_GIB, 2)


def build_override_capabilities(
    *,
    model: str,
    runtime_context_tokens: int,
    source: str = SOURCE_UI_OVERRIDE,
) -> OllamaModelCapabilities:
    """Build a capabilities object from an explicit override."""
    return OllamaModelCapabilities(
        model=model,
        runtime_context_tokens=runtime_context_tokens,
        source=source,
    )


async def detect_ollama_model_capabilities(
    client: httpx.AsyncClient,
    model: str,
) -> OllamaModelCapabilities:
    """Detect the runtime context window and estimated KV-cache size.

    Detection uses ``/api/ps`` as the source of truth. If the model is
    not loaded yet, a lightweight warmup request is sent first, then
    ``/api/ps`` is queried again. ``/api/show`` is read only to enrich
    the result with metadata and a KV-cache estimate.
    """
    show_payload = await _fetch_show_payload(client, model)

    ps_payload = await _fetch_ps_payload(client, model)
    loaded_model = _find_loaded_model(ps_payload, model)
    source = SOURCE_RUNTIME
    if loaded_model is None:
        await _warmup_model(client, model)
        ps_payload = await _fetch_ps_payload(client, model)
        loaded_model = _find_loaded_model(ps_payload, model)
        source = SOURCE_RUNTIME_AFTER_WARMUP

    if loaded_model is None:
        raise OllamaCapabilityError(
            f"Ollama did not report runtime context_length for model {model!r}"
        )

    runtime_context_tokens = _positive_int(loaded_model.get("context_length"))
    if runtime_context_tokens is None:
        raise OllamaCapabilityError(
            f"Ollama returned no positive context_length for model {model!r}"
        )

    model_max_context_tokens = None
    kv_bytes_per_token = None
    if show_payload is not None:
        model_max_context_tokens = _extract_model_max_context(show_payload)
        kv_bytes_per_token = _estimate_kv_bytes_per_token(show_payload)

    estimated_runtime_kv_bytes = None
    if kv_bytes_per_token is not None:
        estimated_runtime_kv_bytes = runtime_context_tokens * kv_bytes_per_token

    return OllamaModelCapabilities(
        model=model,
        runtime_context_tokens=runtime_context_tokens,
        source=source,
        model_max_context_tokens=model_max_context_tokens,
        model_size_bytes=_positive_int(loaded_model.get("size")),
        size_vram_bytes=_positive_int(loaded_model.get("size_vram")),
        kv_bytes_per_token=kv_bytes_per_token,
        estimated_runtime_kv_bytes=estimated_runtime_kv_bytes,
    )


async def _fetch_ps_payload(
    client: httpx.AsyncClient,
    model: str,
) -> dict[str, Any]:
    """Fetch the loaded-model list from ``/api/ps``."""
    try:
        response = await client.get(PS_ENDPOINT)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OllamaCapabilityError(
            f"Failed to query /api/ps for model {model!r}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise OllamaCapabilityError("Ollama /api/ps returned a non-object payload")
    return payload


async def _fetch_show_payload(
    client: httpx.AsyncClient,
    model: str,
) -> dict[str, Any] | None:
    """Fetch ``/api/show`` metadata when available."""
    try:
        response = await client.post(SHOW_ENDPOINT, json={"model": model})
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


async def _warmup_model(client: httpx.AsyncClient, model: str) -> None:
    """Ask Ollama to load the model without forcing a guessed num_ctx."""
    response = await client.post(
        GENERATE_ENDPOINT,
        json={
            "model": model,
            "prompt": WARMUP_PROMPT,
            "stream": WARMUP_STREAM,
            "keep_alive": WARMUP_KEEP_ALIVE,
        },
    )
    response.raise_for_status()


def _find_loaded_model(
    payload: dict[str, Any],
    target_model: str,
) -> dict[str, Any] | None:
    """Return the matching loaded-model entry from ``/api/ps``."""
    raw_models = payload.get("models", [])
    if not isinstance(raw_models, list):
        return None
    for raw_model in raw_models:
        if not isinstance(raw_model, dict):
            continue
        reported_name = raw_model.get("name") or raw_model.get("model")
        if _model_names_match(reported_name, target_model):
            return raw_model
    return None


def _model_names_match(reported: object, target: str) -> bool:
    """Return whether an Ollama-reported name matches the requested model."""
    if not isinstance(reported, str):
        return False
    if reported == target:
        return True
    return reported == f"{target}:latest" or target == f"{reported}:latest"


def _extract_model_max_context(payload: dict[str, Any]) -> int | None:
    """Return the maximum declared context window from ``/api/show``."""
    candidates: list[int] = []

    model_info = payload.get("model_info", {})
    if isinstance(model_info, dict):
        for key, value in model_info.items():
            if key.endswith(".context_length"):
                positive_value = _positive_int(value)
                if positive_value is not None:
                    candidates.append(positive_value)

    parameters = payload.get("parameters", "")
    if isinstance(parameters, str):
        for raw_line in parameters.splitlines():
            parts = raw_line.strip().split()
            if len(parts) >= 2 and parts[0] == "num_ctx":
                parsed_num_ctx = _positive_int(parts[1])
                if parsed_num_ctx is not None:
                    candidates.append(parsed_num_ctx)

    return max(candidates) if candidates else None


def _estimate_kv_bytes_per_token(payload: dict[str, Any]) -> int | None:
    """Estimate KV-cache bytes per token from ``/api/show`` metadata."""
    model_info = payload.get("model_info", {})
    if not isinstance(model_info, dict):
        return None

    architecture = model_info.get("general.architecture")
    if not isinstance(architecture, str) or not architecture:
        return None

    block_count = _metadata_int(model_info, architecture, "block_count")
    head_count = _metadata_int(
        model_info, architecture, "attention.head_count_kv"
    ) or _metadata_int(model_info, architecture, "attention.head_count")
    key_length = _metadata_int(model_info, architecture, "attention.key_length")
    value_length = _metadata_int(model_info, architecture, "attention.value_length")

    if None in (block_count, head_count, key_length, value_length):
        return None

    return block_count * head_count * (key_length + value_length) * KV_ELEMENT_BYTES


def _metadata_int(
    model_info: dict[str, Any],
    architecture: str,
    suffix: str,
) -> int | None:
    """Return one positive integer metadata field or None."""
    return _positive_int(model_info.get(f"{architecture}.{suffix}"))


def _positive_int(value: object) -> int | None:
    """Return a strictly positive integer or None."""
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None
