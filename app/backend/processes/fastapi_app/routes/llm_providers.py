"""LLM configuration routes."""

from typing import TYPE_CHECKING

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from backend.llm.ollama.capabilities import (
    OllamaCapabilityError,
    detect_ollama_model_capabilities,
)
from backend.llm.openai_compatible.protocol import _build_auth_headers

from ..deps import _get_credential_client, _require_auth
from ..problem import SLUG_UPSTREAM_ERROR, typed_http_exception


if TYPE_CHECKING:
    from backend.channels.jwt_auth import TokenClaims

    from ..clients import CredentialClient


llm_router = APIRouter(prefix="/admin/llm")
OPENROUTER_API_PREFIX = "/api/v1"
CUSTOM_API_PREFIX = "/v1"
LLM_CREDENTIAL_FIELDS = (
    "api_key",
    "model",
    "dialog_recorder_model",
    "base_url",
    "dialog_recorder_base_url",
    "embedding_model",
    "embedding_base_url",
    "num_ctx",
)


class ModelListRequest(BaseModel):
    """Request body for listing provider models without persisting secrets."""

    service_type: str = "openrouter"
    api_key: str = ""
    base_url: str = ""
    dialog_recorder_base_url: str = ""


class OllamaCapabilitiesRequest(BaseModel):
    """Request body for probing Ollama runtime capabilities."""

    service_type: str = "ollama"
    model: str = ""
    base_url: str = ""


def _get_api_prefix(service_type: str) -> str:
    """Return the API path prefix for the selected provider."""
    if service_type == "custom":
        return CUSTOM_API_PREFIX
    return OPENROUTER_API_PREFIX


def _require_supported_service_type(service_type: str) -> str:
    """Validate one supported LLM service type."""
    if service_type not in ("openrouter", "ollama", "custom"):
        raise HTTPException(
            status_code=422,
            detail="service_type must be one of: openrouter, ollama, custom",
        )
    return service_type


def _credential_fields(body: dict[str, object]) -> dict[str, str]:
    """Extract non-empty LLM credential fields from one request body."""
    credentials: dict[str, str] = {}
    for key in LLM_CREDENTIAL_FIELDS:
        value = body.get(key)
        if value:
            credentials[key] = str(value)
    return credentials


def _provider_probe_values(body: dict[str, object]) -> dict[str, str]:
    """Extract provider probe inputs from one raw request body."""
    return {
        "service_type": str(body.get("service_type", "openrouter")),
        "api_key": str(body.get("api_key", "")),
        "model": str(body.get("model", "")),
        "base_url": str(body.get("base_url", "")),
        "probe": str(body.get("probe", "chat")),
    }


def _require_existing_config(full: dict[str, object]) -> dict[str, object]:
    """Return one saved config or raise 404 if it does not exist."""
    if full.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="Configuration not found")
    return full


def _require_config_operation_result(result: dict[str, object]) -> dict[str, object]:
    """Return one config operation result or raise 404 when it is missing."""
    if result.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="Configuration not found")
    return result


async def _reload_llm_runtime(request: Request) -> dict[str, object]:
    """Ask orchestrator to reload LLM runtime from saved configuration."""
    orch_client = getattr(request.app.state, "orchestrator_client", None)
    if orch_client is None or not orch_client.is_connected:
        return {"success": False, "error": "orchestrator not connected"}

    import uuid

    try:
        reload_result = await orch_client.send_reload_llm(request_id=str(uuid.uuid4()))
    except Exception as error:
        return {"success": False, "error": str(error)[:200]}
    return {
        "success": reload_result.success,
        "active_provider": reload_result.active_provider,
        "model": reload_result.model,
        "error": reload_result.error,
    }


def _reload_detail(
    *,
    applied_detail: str,
    restart_detail: str,
    failed_detail: str,
    reload_result: dict[str, object],
) -> str:
    """Return user-facing operation detail from one reload result."""
    if reload_result.get("success"):
        return applied_detail
    if reload_result.get("error") == "orchestrator not connected":
        return restart_detail
    return failed_detail


async def _probe_embedding(
    *,
    service_type: str,
    api_key: str,
    model: str,
    base_url: str,
) -> dict:
    """Embed one word on the target host and report the vector dimension.

    Unlike the chat probe, this proves the configured model actually
    produces embeddings — a chat model on a live host passes an address
    check but fails here.
    """
    import httpx

    if not model:
        return {"ok": False, "error": "embedding model is required"}
    try:
        if service_type == "ollama":
            url = (
                base_url.rstrip("/") if base_url else "http://ollama:11434"
            ) + "/api/embed"
            payload: dict[str, object] = {"model": model, "input": "ping"}
            headers = {"Content-Type": "application/json"}
        else:
            endpoint_base = (
                base_url.rstrip("/") if base_url else "https://openrouter.ai"
            )
            url = endpoint_base + _get_api_prefix(service_type) + "/embeddings"
            payload = {"model": model, "input": "ping"}
            headers = {
                **_build_auth_headers(api_key),
                "Content-Type": "application/json",
            }
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code not in (200, 201):
            return {
                "ok": False,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }
        data = resp.json()
        if service_type == "ollama":
            vectors = data.get("embeddings") or []
            vector = vectors[0] if vectors else []
        else:
            entries = data.get("data") or []
            vector = entries[0].get("embedding", []) if entries else []
        if not vector:
            return {"ok": False, "error": "server returned an empty embedding"}
        return {
            "ok": True,
            "provider": service_type,
            "dimension": len(vector),
        }
    except httpx.TimeoutException:
        return {"ok": False, "error": "Connection timed out"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _probe_llm_provider(
    *,
    service_type: str,
    api_key: str,
    model: str,
    base_url: str,
    probe: str = "chat",
) -> dict:
    """Run one lightweight provider connectivity probe."""
    import httpx

    if probe == "embedding":
        return await _probe_embedding(
            service_type=service_type,
            api_key=api_key,
            model=model,
            base_url=base_url,
        )
    try:
        if service_type == "ollama":
            url = (
                base_url.rstrip("/") if base_url else "http://ollama:11434"
            ) + "/api/tags"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
            if resp.status_code == 200:
                return {"ok": True, "provider": "ollama"}
            return {"ok": False, "error": f"Ollama returned HTTP {resp.status_code}"}

        endpoint_base = base_url.rstrip("/") if base_url else "https://openrouter.ai"
        url = endpoint_base + _get_api_prefix(service_type) + "/chat/completions"
        payload = {
            "model": model or "openai/gpt-4o-mini",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }
        headers = {
            **_build_auth_headers(api_key),
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code in (200, 201):
            return {"ok": True, "provider": service_type}
        body_text = resp.text[:200]
        return {"ok": False, "error": f"HTTP {resp.status_code}: {body_text}"}

    except httpx.TimeoutException:
        return {"ok": False, "error": "Connection timed out"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def _list_provider_models(
    *,
    service_type: str,
    api_key: str,
    base_url: str,
) -> dict:
    """List available models for one provider.

    Raises:
        HTTPException: 502 on transport or upstream-5xx failure; 400
            (upstream-error) when the provider rejects the request with
            a 4xx status — the upstream status is never passed through.
    """
    import httpx

    try:
        if service_type == "ollama":
            url = (
                base_url.rstrip("/") if base_url else "http://ollama:11434"
            ) + "/api/tags"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            return {"models": [str(model["name"]) for model in data.get("models", [])]}

        headers = _build_auth_headers(api_key)
        if service_type == "openrouter":
            endpoint_base = (
                base_url.rstrip("/") if base_url else "https://openrouter.ai"
            )
            url = endpoint_base + OPENROUTER_API_PREFIX + "/models"
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return {
                "models": sorted(str(model["id"]) for model in data.get("data", []))
            }

        url = base_url.rstrip("/") + CUSTOM_API_PREFIX + "/models"
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code == 404:
            return {"models": [], "record": "Provider does not support model listing"}
        resp.raise_for_status()
        data = resp.json()
        return {"models": sorted(str(model["id"]) for model in data.get("data", []))}

    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=502,
            detail="Connection timed out",
        ) from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code < 500:
            raise typed_http_exception(
                slug=SLUG_UPSTREAM_ERROR,
                status_code=400,
                detail=str(exc),
            ) from exc
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def _detect_ollama_runtime_capabilities(
    *,
    base_url: str,
    model: str,
) -> dict[str, object]:
    """Probe Ollama for the runtime context window and KV estimate."""
    resolved_base_url = base_url.rstrip("/") if base_url else "http://ollama:11434"
    async with httpx.AsyncClient(timeout=20.0, base_url=resolved_base_url) as client:
        detected = await detect_ollama_model_capabilities(client, model)
    return {
        "model": detected.model,
        "source": detected.source,
        "runtime_context_tokens": detected.runtime_context_tokens,
        "model_max_context_tokens": detected.model_max_context_tokens,
        "model_size_bytes": detected.model_size_bytes,
        "size_vram_bytes": detected.size_vram_bytes,
        "kv_bytes_per_token": detected.kv_bytes_per_token,
        "estimated_runtime_kv_bytes": detected.estimated_runtime_kv_bytes,
        "estimated_runtime_kv_gib": detected.estimated_runtime_kv_gib,
    }


def _model_not_found_detail(exc: httpx.HTTPStatusError, model: str) -> str:
    """Extract the upstream not-found message for one requested model."""
    try:
        payload = exc.response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        message = payload.get("error")
        if isinstance(message, str) and message:
            return message
    return f"Model '{model}' not found on host"


@llm_router.get("/config")
async def get_llm_config(
    claims: TokenClaims = Depends(_require_auth),
    cred: CredentialClient = Depends(_get_credential_client),
) -> dict:
    """Return current LLM provider configuration (no secrets).

    Returns provider type, model, dialog_recorder_model, base URLs, and num_ctx.
    Never returns API keys or credential values.
    """
    del claims
    return await cred.get_llm_config_meta()


@llm_router.put("/config")
async def save_llm_config(
    request: Request,
    claims: TokenClaims = Depends(_require_auth),
    cred: CredentialClient = Depends(_get_credential_client),
) -> dict:
    """Save LLM provider configuration.

    Requires an active session. Atomically upserts the integration and all credentials.
    """
    del claims
    body = await request.json()
    service_type = _require_supported_service_type(str(body.get("service_type", "")))
    credentials = _credential_fields(body)
    await cred.store_llm_config(service_type, credentials)
    return {"detail": "LLM configuration saved"}


@llm_router.post("/test")
async def probe_llm_connection(
    request: Request,
    claims: TokenClaims = Depends(_require_auth),
) -> dict:
    """Test connectivity to an LLM provider using credentials from the request body.

    Accepts service_type, api_key, model, and optional base_url directly from
    the form — no need to save first. Sends a minimal chat completion request.
    Returns ok=True on success or ok=False with error message.
    """
    del claims
    body = await request.json()
    return await _probe_llm_provider(**_provider_probe_values(body))


@llm_router.post("/models")
async def list_llm_models(
    body: ModelListRequest,
    claims: TokenClaims = Depends(_require_auth),
) -> dict:
    """Return available models from the LLM provider.

    Accepts credentials in the request body (never stored).
    Failures surface as RFC 9457 problem responses: 502 on transport or
    upstream-5xx failure, 400 (upstream-error) on an upstream 4xx response;
    a custom provider without a model list returns 200 with a record.
    """
    del claims
    return await _list_provider_models(
        service_type=body.service_type,
        api_key=body.api_key,
        base_url=body.base_url,
    )


@llm_router.post("/capabilities")
async def detect_llm_capabilities(
    body: OllamaCapabilitiesRequest,
    claims: TokenClaims = Depends(_require_auth),
) -> dict[str, object]:
    """Return runtime capabilities for an Ollama model.

    Provider and network failures surface as a 502 problem response; a
    model the host does not know surfaces as a 404 problem response.
    """
    del claims
    if body.service_type != "ollama":
        raise HTTPException(
            status_code=422,
            detail="Runtime capability detection is implemented only for ollama",
        )
    if not body.model:
        raise HTTPException(status_code=422, detail="model is required")
    try:
        return await _detect_ollama_runtime_capabilities(
            base_url=body.base_url,
            model=body.model,
        )
    except OllamaCapabilityError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(
                status_code=404,
                detail=_model_not_found_detail(exc, body.model),
            ) from exc
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@llm_router.get("/configs")
async def list_llm_configs(
    claims: TokenClaims = Depends(_require_auth),
    cred: CredentialClient = Depends(_get_credential_client),
) -> dict:
    """Return all saved LLM configurations (no secrets)."""
    del claims
    return await cred.list_llm_configs()


@llm_router.post("/configs")
async def create_llm_config(
    request: Request,
    claims: TokenClaims = Depends(_require_auth),
    cred: CredentialClient = Depends(_get_credential_client),
) -> JSONResponse:
    """Create a new LLM configuration (inactive by default).

    Returns 201 with the created configuration path in Location.
    """
    del claims
    body = await request.json()
    service_type = _require_supported_service_type(str(body.get("service_type", "")))
    credentials = _credential_fields(body)
    result = await cred.create_llm_config(service_type, credentials)
    config_id = result.get("id")
    return JSONResponse(
        status_code=201,
        headers={"Location": f"/admin/llm/configs/{config_id}"},
        content={"id": config_id, "detail": "Configuration created"},
    )


@llm_router.patch("/configs/{config_id}")
async def update_llm_config(
    config_id: str,
    request: Request,
    claims: TokenClaims = Depends(_require_auth),
    cred: CredentialClient = Depends(_get_credential_client),
) -> dict:
    """Update an existing LLM configuration.

    Merge semantics: absent fields keep their stored value; validation
    applies to the provided fields.
    """
    del claims
    body = await request.json()
    credentials = _credential_fields(body)
    _require_config_operation_result(
        await cred.update_llm_config(config_id, credentials)
    )
    reload_result = await _reload_llm_runtime(request)
    return {
        "detail": _reload_detail(
            applied_detail="Configuration updated and applied.",
            restart_detail="Configuration updated. Restart to apply.",
            failed_detail="Configuration updated. Reload failed.",
            reload_result=reload_result,
        ),
        "reload": reload_result,
    }


@llm_router.delete("/configs/{config_id}")
async def delete_llm_config(
    config_id: str,
    request: Request,
    claims: TokenClaims = Depends(_require_auth),
    cred: CredentialClient = Depends(_get_credential_client),
) -> Response:
    """Delete an LLM configuration and its credentials.

    Returns 204 without a body; the runtime reload still runs as a side effect.
    """
    del claims
    _require_config_operation_result(await cred.delete_llm_config(config_id))
    await _reload_llm_runtime(request)
    return Response(status_code=204)


@llm_router.post("/configs/{config_id}/activate")
async def activate_llm_config(
    config_id: str,
    request: Request,
    claims: TokenClaims = Depends(_require_auth),
    cred: CredentialClient = Depends(_get_credential_client),
) -> dict:
    """Set an LLM configuration as active and reload the runtime when possible."""
    del claims
    _require_config_operation_result(await cred.activate_llm_config(config_id))
    reload_result = await _reload_llm_runtime(request)
    return {
        "detail": _reload_detail(
            applied_detail="Configuration applied.",
            restart_detail="Configuration activated. Restart to apply.",
            failed_detail="Configuration activated. Reload failed.",
            reload_result=reload_result,
        ),
        "reload": reload_result,
    }


@llm_router.post("/configs/{config_id}/test")
async def probe_saved_llm_config(
    config_id: str,
    claims: TokenClaims = Depends(_require_auth),
    cred: CredentialClient = Depends(_get_credential_client),
) -> dict:
    """Test connectivity using stored credentials of a saved configuration."""
    del claims
    full = _require_existing_config(await cred.get_llm_config_full(config_id))
    service_type = full["service_type"]
    credentials = full.get("credentials", {})
    return await _probe_llm_provider(
        service_type=str(service_type),
        api_key=str(credentials.get("api_key", "")),
        model=str(credentials.get("model", "")),
        base_url=str(credentials.get("base_url", "")),
    )
