"""Ollama-specific spec for the generic kv-cache (context window) detector.

Contains the small Ollama-only pieces consumed by
``app/backend/llm/kv_detector.py``: the extractor that reads
``context_length`` from a loaded-model entry in ``/api/ps``, the warmup
callable that forces a model load via an empty ``/api/generate`` POST,
and the singleton ``OLLAMA_DETECTOR`` constant. Everything universal
(HTTP requests, error wrapping, retry logic, source labels) lives in
the parent generic module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.llm.kv_detector import HttpDetectorSpec


if TYPE_CHECKING:
    from collections.abc import Iterable

    import httpx


# --- Provider identity ---
OLLAMA_PROVIDER_NAME = "ollama"

# --- Endpoint paths ---
PS_ENDPOINT = "/api/ps"
GENERATE_ENDPOINT = "/api/generate"
SHOW_ENDPOINT = "/api/show"

# --- Source identifier stamped on resolved capabilities ---
SOURCE_PS = "ollama:/api/ps"

# --- Warmup request shape ---
WARMUP_KEEP_ALIVE = "5m"
WARMUP_PROMPT = ""
WARMUP_STREAM = False


def _extract_ollama(body: dict[str, Any], model: str) -> int | None:
    """Return the positive ``context_length`` for ``model`` if loaded."""
    raw_models = body.get("models", [])
    if not isinstance(raw_models, list):
        return None
    return _find_context_length(
        (entry for entry in raw_models if isinstance(entry, dict)),
        model,
    )


async def _fetch_model_max_context(
    client: httpx.AsyncClient,
    model: str,
) -> int | None:
    """Query ``/api/show`` for the model's maximum context_length.

    Used **only** as a hint for the warmup request so Ollama loads the
    model with the full context window instead of its internal 4096
    default. The returned value is NOT the final resolved capacity —
    that always comes from ``/api/ps`` after the model is loaded.

    Returns ``None`` on any failure so the warmup proceeds without a
    hint (Ollama will load with its default).
    """
    try:
        response = await client.post(
            SHOW_ENDPOINT,
            json={"model": model},
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return _parse_max_context_from_show(data)


def _parse_max_context_from_show(data: dict[str, Any]) -> int | None:
    """Extract the largest positive context_length from /api/show metadata.

    Checks ``model_info`` keys ending in ``.context_length`` and the
    ``parameters`` text block for a ``num_ctx`` line. Returns the largest
    positive value found, or ``None``.
    """
    candidates: list[int] = []

    model_info = data.get("model_info", {})
    if isinstance(model_info, dict):
        for key, value in model_info.items():
            if key.endswith(".context_length") and isinstance(value, int) and value > 0:
                candidates.append(value)

    parameters = data.get("parameters", "")
    if isinstance(parameters, str):
        for line in parameters.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0] == "num_ctx":
                try:
                    parsed = int(parts[1])
                except ValueError:
                    continue
                if parsed > 0:
                    candidates.append(parsed)

    return max(candidates) if candidates else None


async def _warmup_ollama(client: httpx.AsyncClient, model: str) -> None:
    """Force Ollama to load ``model`` with its full context window.

    First queries ``/api/show`` to discover the model's maximum
    context_length from the weight metadata. That value is passed as
    ``options.num_ctx`` in the warmup ``/api/generate`` request so
    Ollama allocates the KV cache at the model's actual capacity
    instead of the internal 4096 default. If ``/api/show`` fails or
    returns no usable value, the warmup proceeds without a hint.
    """
    hint = await _fetch_model_max_context(client, model)
    payload: dict[str, Any] = {
        "model": model,
        "prompt": WARMUP_PROMPT,
        "stream": WARMUP_STREAM,
        "keep_alive": WARMUP_KEEP_ALIVE,
    }
    if hint is not None:
        payload["options"] = {"num_ctx": hint}
    response = await client.post(GENERATE_ENDPOINT, json=payload)
    response.raise_for_status()


def _find_context_length(
    models: Iterable[dict[str, Any]],
    target_model: str,
) -> int | None:
    """Return the positive context_length for ``target_model`` if loaded."""
    for entry in models:
        name = entry.get("name") or entry.get("model")
        if not _model_names_match(name, target_model):
            continue
        context_length = entry.get("context_length")
        if isinstance(context_length, int) and context_length > 0:
            return context_length
    return None


def _model_names_match(reported: object, target: str) -> bool:
    """Return True if Ollama's reported name matches the target model.

    Ollama may return either the bare name or the ``name:latest`` form when
    the user requested the model without an explicit tag.
    """
    if not isinstance(reported, str):
        return False
    if reported == target:
        return True
    return reported == f"{target}:latest" or target == f"{reported}:latest"


OLLAMA_DETECTOR = HttpDetectorSpec(
    provider_name=OLLAMA_PROVIDER_NAME,
    path=PS_ENDPOINT,
    extract=_extract_ollama,
    source_label=SOURCE_PS,
    warmup=_warmup_ollama,
)
