"""Agent token management routes (admin/agents).

Provides endpoints for issuing, listing, revoking MCP agent tokens,
downloading the public CA root certificate, and generating a ready-to-paste
connection configuration block. All endpoints are auth-guarded like the
rest of the /admin routes and route through the Credential Proxy (Socket C).
"""

# ruff: noqa: B008

import hashlib
import os
import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from ..deps import (
    _get_credential_client,
    _get_reauth_manager,
    _require_auth,
)


if TYPE_CHECKING:
    from backend.channels.endpoints import ReauthManager

    from ..clients import CredentialClient


agents_router = APIRouter(prefix="/admin/agents")

# Public CA root certificate path (set by generate_dev_tls.sh).
_CA_ROOT_PATH = Path(os.environ.get("BIMEMO_CA_ROOT_PATH", ""))


def _generate_token() -> str:
    """Generate a high-entropy random token string."""
    return secrets.token_hex(32)


def _hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of a plaintext token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@agents_router.get("/list")
async def list_agent_tokens(
    request: Request,
    cred: CredentialClient = Depends(_get_credential_client),
) -> list[dict[str, object]]:
    """List all configured MCP agent tokens (metadata only).

    Returns label, capability, and created_at — never the plaintext token.

    Args:
        request: FastAPI request (for auth guard).
        cred: Credential Proxy client.

    Returns:
        List of agent token metadata dicts.
    """
    _require_auth(request)
    integrations = await cred.list_integrations()
    result: list[dict[str, object]] = []
    for entry in integrations:
        if entry.get("service_type") != "mcp_agent":
            continue
        config = entry.get("config") or {}
        result.append(
            {
                "label": entry.get("name", ""),
                "capability": config.get("capability", ""),
                "created_at": entry.get("created_at", ""),
            }
        )
    return result


@agents_router.post("/issue", status_code=201)
async def issue_agent_token(
    request: Request,
    label: str,
    capability: str = "read",
    reauth_token: str | None = Header(None, alias="X-Reauth-Token"),
    cred: CredentialClient = Depends(_get_credential_client),
    reauth_mgr: ReauthManager = Depends(_get_reauth_manager),
) -> dict[str, str]:
    """Issue a new MCP agent token.

    Generates a high-entropy random token, stores only its SHA-256 hash,
    and returns the plaintext exactly once. The plaintext is never logged.
    Issuing an access credential requires a valid single-use
    re-authentication token, like the revoke path.

    Args:
        request: FastAPI request (for auth guard).
        label: Human-readable label for the token (e.g. "claude").
        capability: Token capability — "read" or "read-write".
        reauth_token: Single-use reauth token from X-Reauth-Token header.
        cred: Credential Proxy client.
        reauth_mgr: Re-authentication manager.

    Returns:
        Dict with "label" and "token" (plaintext, shown once).

    Raises:
        HTTPException: 403 without a valid reauth token, 400 on invalid
            capability, 409 if the label already exists.
    """
    _require_auth(request)

    # Issuing an access credential is sensitive: require a valid, single-use
    # re-authentication token (same posture as revoke).
    if not reauth_token or not reauth_mgr.validate_and_consume(reauth_token):
        raise HTTPException(
            status_code=403,
            detail="Valid re-authentication required to issue a token.",
        )

    if capability not in ("read", "read-write"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid capability '{capability}'. "
                "Must be 'read' or 'read-write'."
            ),
        )

    # Check for existing token with this label
    integrations = await cred.list_integrations()
    for entry in integrations:
        if entry.get("service_type") == "mcp_agent" and entry.get("name") == label:
            raise HTTPException(
                status_code=409,
                detail=f"Agent token '{label}' already exists.",
            )

    plaintext = _generate_token()
    token_hash = _hash_token(plaintext)

    await cred.create_integration(
        name=label,
        integration_type="mcp_agent",
        config={"token_hash": token_hash, "capability": capability},
    )

    # Do NOT log the plaintext token
    return {"label": label, "token": plaintext}


@agents_router.delete("/revoke/{label}")
async def revoke_agent_token(
    request: Request,
    label: str,
    reauth_token: str | None = Header(None, alias="X-Reauth-Token"),
    cred: CredentialClient = Depends(_get_credential_client),
    reauth_mgr: ReauthManager = Depends(_get_reauth_manager),
) -> dict[str, str]:
    """Revoke an MCP agent token by label.

    Deletes the corresponding ``mcp_agent`` integration so the token no longer
    authenticates. Requires a valid single-use re-authentication token, and
    only ever deletes an ``mcp_agent`` integration — never a non-agent
    integration that happens to share the same name.

    Args:
        request: FastAPI request (for auth guard).
        label: The label of the token to revoke.
        reauth_token: Single-use reauth token from X-Reauth-Token header.
        cred: Credential Proxy client.
        reauth_mgr: Re-authentication manager.

    Returns:
        Dict with confirmation message.

    Raises:
        HTTPException: 403 without a valid reauth token, 404 if no agent token
            with that label exists.
    """
    _require_auth(request)

    # Destructive action: require a valid, single-use re-authentication token.
    if not reauth_token or not reauth_mgr.validate_and_consume(reauth_token):
        raise HTTPException(
            status_code=403,
            detail="Valid re-authentication required for token revocation.",
        )

    # Only revoke agent tokens. delete_integration matches by name across all
    # service types, so confirm the target is an mcp_agent before deleting —
    # otherwise a label like "openrouter" would drop an LLM integration.
    integrations = await cred.list_integrations()
    is_agent_token = any(
        entry.get("service_type") == "mcp_agent" and entry.get("name") == label
        for entry in integrations
    )
    if not is_agent_token:
        raise HTTPException(
            status_code=404,
            detail=f"No agent token '{label}' found.",
        )

    await cred.delete_integration(label)
    return {"detail": f"Agent token '{label}' revoked."}


@agents_router.get("/ca-root")
def download_ca_root(request: Request) -> dict[str, str]:
    """Return the public CA root certificate for download.

    Serves only the public CA certificate — never the private key.
    The private key lives on the host only, never in the web container.

    Args:
        request: FastAPI request (for auth guard).

    Returns:
        Dict with the PEM-encoded public CA certificate.

    Raises:
        HTTPException: 503 if CA root is not configured.
    """
    _require_auth(request)

    if not _CA_ROOT_PATH or not _CA_ROOT_PATH.is_file():
        raise HTTPException(
            status_code=503,
            detail="CA root certificate is not configured.",
        )

    # Only read the public cert — never the key
    pem = _CA_ROOT_PATH.read_text(encoding="utf-8")
    return {"certificate": pem}


@agents_router.get("/config-block")
async def get_connection_config(
    request: Request,
    label: str,
    cred: CredentialClient = Depends(_get_credential_client),
) -> dict[str, str]:
    """Generate a ready-to-paste connection configuration block.

    Returns the TLS MCP URL and the token label for an external agent
    to configure its connection.

    Args:
        request: FastAPI request (for auth guard).
        label: The label of the agent token.
        cred: Credential Proxy client.

    Returns:
        Dict with "url" (TLS MCP endpoint) and "label".

    Raises:
        HTTPException: 404 if the agent token does not exist.
    """
    _require_auth(request)

    integrations = await cred.list_integrations()
    found = False
    for entry in integrations:
        if entry.get("service_type") == "mcp_agent" and entry.get("name") == label:
            found = True
            break

    if not found:
        raise HTTPException(
            status_code=404,
            detail=f"Agent token '{label}' not found.",
        )

    # Build the TLS MCP URL
    host = request.headers.get(
        "x-forwarded-host", request.headers.get("host", "localhost")
    )
    url = f"https://{host}/mcp"

    return {"url": url, "label": label}
