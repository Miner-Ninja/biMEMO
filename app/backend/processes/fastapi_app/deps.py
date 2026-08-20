"""Dependency injection helpers for FastAPI routes."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request


if TYPE_CHECKING:
    from backend.channels.endpoints import ReauthManager
    from backend.channels.jwt_auth import SessionManager, TokenClaims
    from backend.channels.realtime import RealtimeConnectionManager
    from backend.chat_bridge import ChatBridgeService
    from backend.config import Settings

    from .clients import (
        AdminDBClient,
        CredentialClient,
        OrchestratorClient,
        SigningClient,
    )


# CSRF token store (module-level, per-process)
_csrf_tokens: dict[str, str] = {}
BEARER_PREFIX = "Bearer "
LOCAL_BRIDGE_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def _app_state_value(request: Request, name: str) -> object:
    """Return one attribute from FastAPI application state."""
    return getattr(request.app.state, name)


def _get_csrf_token_for_jti(jti: str) -> str | None:
    """Look up the CSRF token for a session JTI."""
    return _csrf_tokens.get(jti)


def _request_client_host(request: Request) -> str:
    """Return the request client host or an empty string."""
    client = request.client
    return "" if client is None else str(client.host)


def _require_auth(request: Request) -> TokenClaims:
    """Extract authenticated claims from request state.

    Args:
        request: FastAPI request.

    Returns:
        Validated TokenClaims.

    Raises:
        HTTPException: 401 if claims not present.
    """
    claims = request.state._state.get("claims")
    if claims is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )
    return claims


def _get_orchestrator_client(request: Request) -> OrchestratorClient:
    """Return the Orchestrator IPC client.

    Raises:
        HTTPException: 503 if Orchestrator is disconnected.
    """
    client = _app_state_value(request, "orchestrator_client")
    if not client.is_connected:
        raise HTTPException(
            status_code=503,
            detail="Orchestrator unavailable",
        )
    return client


def _get_admin_client(request: Request) -> AdminDBClient:
    """Return the DB Proxy Socket B client."""
    return _app_state_value(request, "admin_client")


def _get_credential_client(request: Request) -> CredentialClient:
    """Return the Credential Proxy client."""
    client = _app_state_value(request, "credential_client")
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="Credential proxy unavailable",
        )
    return client


def _get_signing_client(request: Request) -> SigningClient:
    """Return the Signing Proxy client."""
    client = _app_state_value(request, "signing_client")
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="Signing proxy unavailable",
        )
    return client


def _get_session_manager(request: Request) -> SessionManager:
    """Return the session manager."""
    return _app_state_value(request, "session_manager")


def _get_realtime_manager(request: Request) -> RealtimeConnectionManager:
    """Return the realtime connection manager."""
    return _app_state_value(request, "realtime_manager")


def _get_reauth_manager(request: Request) -> ReauthManager:
    """Return the re-authentication manager."""
    return _app_state_value(request, "reauth_manager")


def _get_chat_bridge_service(request: Request) -> ChatBridgeService:
    """Return the configured chat bridge service."""
    service = _app_state_value(request, "chat_bridge_service")
    if service is None:
        raise HTTPException(
            status_code=503,
            detail="Chat bridge service unavailable",
        )
    return service


def _get_optional_chat_bridge_service(
    request: Request,
) -> ChatBridgeService | None:
    """Return the configured chat bridge service, if available."""
    return _app_state_value(request, "chat_bridge_service")


def _bridge_bearer_token(settings: Settings, provider: str) -> str:
    """Return the configured local bridge token for one provider."""
    normalized_provider = provider.strip().lower()
    if normalized_provider == "discord":
        return str(getattr(settings, "discord_bridge_token", "")).strip()
    return ""


def _bridge_auth_not_configured_detail(provider: str) -> str:
    """Return the user-facing error when one provider bridge token is missing."""
    normalized_provider = provider.strip().lower()
    if normalized_provider == "discord":
        return "Discord bridge inbound auth is not configured"
    return "Bridge inbound auth is not configured"


def _require_chat_bridge_bearer(
    request: Request,
    provider: str,
) -> None:
    """Require one local caller with the configured bridge token."""
    settings = request.app.state.settings
    configured_token = _bridge_bearer_token(settings, provider)
    if not configured_token:
        raise HTTPException(
            status_code=503,
            detail=_bridge_auth_not_configured_detail(provider),
        )
    client_host = _request_client_host(request)
    if client_host not in LOCAL_BRIDGE_HOSTS:
        raise HTTPException(
            status_code=403,
            detail="Bridge caller must be local",
        )
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith(BEARER_PREFIX):
        raise HTTPException(
            status_code=401,
            detail="Bearer token required",
        )
    presented_token = authorization[len(BEARER_PREFIX) :].strip()
    if not presented_token or not secrets.compare_digest(
        presented_token,
        configured_token,
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid bridge token",
        )


def _get_audit_fn(request: Request) -> object:
    """Return the audit function for fire-and-forget security event logging."""
    return _app_state_value(request, "audit_fn")
