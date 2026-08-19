"""Admin routes (integrations, conversations, profile)."""

# ruff: noqa: B008

import base64
import mimetypes
import os
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, Response

from backend.channels.endpoints import BackupRateLimiter
from backend.chat_bridge import BridgeProvider, ChatBridgeService
from backend.ipc.models import StreamEventType
from backend.models.requests import (  # noqa: TC001
    ChatBridgeCreateRequest,
    ChatBridgeUpdateRequest,
    IntegrationCreateRequest,
    ReauthRequest,
    UiSettingsRequest,
    UserProfileRequest,
)
from backend.models.responses import (
    ChatBridgeLinkItem,
    ChatBridgeMessageLinkItem,
    ChatBridgeProviderItem,
    IntegrationListItem,
)

from ..deps import (
    _get_admin_client,
    _get_chat_bridge_service,
    _get_credential_client,
    _get_orchestrator_client,
    _get_reauth_manager,
    _get_signing_client,
    _require_auth,
)


if TYPE_CHECKING:
    from backend.channels.endpoints import ReauthManager
    from backend.channels.jwt_auth import TokenClaims

    from ..clients import (
        AdminDBClient,
        CredentialClient,
        OrchestratorClient,
        SigningClient,
    )


admin_router = APIRouter(prefix="/admin")
_backup_rate_limiter = BackupRateLimiter()
FRONTEND_DIST = Path(os.environ.get("PYTHONPATH", "/app")) / "frontend" / "dist"


def _spa_index_response(request: Request) -> FileResponse | None:
    """Return frontend index.html for browser navigations to SPA-backed routes."""
    accept = request.headers.get("accept", "")
    index_html = FRONTEND_DIST / "index.html"
    if "text/html" not in accept or not index_html.is_file():
        return None
    return FileResponse(
        index_html,
        headers={"Cache-Control": "no-cache"},
    )


def _require_request_auth(request: Request) -> TokenClaims:
    """Return request claims or raise 401 when the request is unauthenticated."""
    claims = request.state._state.get("claims")
    if claims is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
        )
    return claims


def _detail_response(detail: str) -> dict[str, str]:
    """Build one short success response with a detail message."""
    return {"detail": detail}


def _extract_proxy_error(detail: object) -> tuple[str, int | None]:
    """Unpack proxy error detail and optional retry-after seconds."""
    if isinstance(detail, dict):
        error = str(detail.get("error", ""))
        retry_after = detail.get("retry_after")
        if isinstance(retry_after, (int, float)):
            return error, int(retry_after)
        return error, None
    return str(detail), None


def _require_reauth_token(x_reauth_token: str | None = Header(default=None)) -> str:
    """Extract the re-authentication token from a request header."""
    if not x_reauth_token:
        raise HTTPException(
            status_code=403,
            detail="Re-authentication required",
        )
    return x_reauth_token


def _require_valid_reauth(
    reauth_mgr: ReauthManager,
    reauth_token: str,
) -> None:
    """Validate and consume one re-authentication token."""
    if not reauth_mgr.validate_and_consume(reauth_token):
        raise HTTPException(
            status_code=403,
            detail="Re-authentication required",
        )


def _integration_list_item(item: dict[str, object]) -> IntegrationListItem:
    """Build one integration response item."""
    return IntegrationListItem(name=str(item["name"]), type=str(item["type"]))


def _chat_bridge_link_item(item: object) -> ChatBridgeLinkItem:
    """Build one chat-bridge response item from stored data or service model."""
    if isinstance(item, dict):
        return ChatBridgeLinkItem(**item)
    return ChatBridgeLinkItem(
        id=item.id,
        conversation_id=item.conversation_id,
        provider=item.provider.value,
        external_space_id=item.external_space_id,
        external_channel_id=item.external_channel_id,
        created_by=item.created_by,
        sync_direction=item.sync_direction.value,
        status=item.status.value,
        delivery_mode=item.delivery_mode.value,
        persona_mode=item.persona_mode.value,
        persona_user_id=item.persona_user_id,
        created_at=item.created_at,
    )


def _chat_bridge_provider_item(
    provider: BridgeProvider,
    *,
    default_persona_user_id: str = "",
) -> ChatBridgeProviderItem:
    """Build one configured bridge-provider item for the web UI."""
    return ChatBridgeProviderItem(
        provider=provider.value,
        default_persona_user_id=default_persona_user_id,
    )


def _chat_bridge_message_link_item(
    item: dict[str, object],
) -> ChatBridgeMessageLinkItem:
    """Build one stored bridge-message mapping response item."""
    return ChatBridgeMessageLinkItem(**item)


def _chat_bridge_for_conversation_response(
    bridge_link: ChatBridgeLinkItem | None,
) -> ChatBridgeLinkItem | dict[str, None]:
    """Build one conversation bridge lookup response."""
    if bridge_link is None:
        return {"chat_bridge_link": None}
    return bridge_link


def _bridge_link_updated_event_data(
    bridge_link: ChatBridgeLinkItem,
) -> dict[str, object]:
    """Build one direct bridge-link update event for the open web chat."""
    return {
        "conversation_id": bridge_link.conversation_id,
        "bridge_link": bridge_link.model_dump(),
    }


@admin_router.get("/settings/ui")
async def get_ui_settings(
    claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict:
    """Return the persisted UI settings blob."""
    del claims
    return {"settings": await admin.get_ui_settings()}


@admin_router.put("/settings/ui")
async def store_ui_settings(
    body: UiSettingsRequest,
    claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict:
    """Replace the persisted UI settings blob.

    Raises:
        HTTPException: 413 when the blob exceeds the server-side cap.
    """
    del claims
    try:
        await admin.store_ui_settings(body.settings)
    except HTTPException as exc:
        # The DB-proxy client wraps operation errors into HTTPException(500)
        # with the operation's message in detail — surface the size-cap
        # rejection as 413 so the client can react specifically.
        if "exceeds" in str(exc.detail):
            raise HTTPException(status_code=413, detail=str(exc.detail)) from exc
        raise
    return {"ok": True}


@admin_router.post("/backup")
async def create_backup(
    claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict:
    """Write consistent encrypted copies of every open database.

    Uses VACUUM INTO through the DB proxy, so the copies are taken from
    the live connections and cannot capture a torn WAL state. Limited to
    3 runs per hour.

    Raises:
        HTTPException: 429 (with Retry-After) when the hourly backup limit is
        exhausted.
    """
    del claims
    if not _backup_rate_limiter.is_allowed():
        raise HTTPException(
            status_code=429,
            detail="Backup limit reached (3 per hour)",
            headers={"Retry-After": str(_backup_rate_limiter.retry_after())},
        )
    result = await admin.create_backup()
    _backup_rate_limiter.record()
    return {"backup_paths": result.get("backup_paths", {})}


@admin_router.post("/reauth")
async def admin_reauth(
    body: ReauthRequest,
    claims: TokenClaims = Depends(_require_auth),
    reauth_mgr: ReauthManager = Depends(_get_reauth_manager),
    signing_client: SigningClient = Depends(_get_signing_client),
) -> dict:
    """Issue a re-authentication token after password verification.

    Args:
        body: Request body containing password.
        claims: Authenticated token claims.
        reauth_mgr: Re-authentication manager.
        signing_client: Signing Proxy client for password verification.

    Returns:
        Dict with reauth_token for use in sensitive operations.

    Raises:
        HTTPException: 401 if password invalid, 429 if rate limited.
    """
    del claims
    try:
        await signing_client.check_password(body.password)
    except HTTPException as exc:
        detail, retry_after = _extract_proxy_error(exc.detail)
        if "rate_limited" in detail:
            raise HTTPException(
                status_code=429,
                detail="Too many attempts",
                headers={"Retry-After": str(retry_after or 30)},
            ) from None
        raise HTTPException(status_code=401, detail="Invalid password") from None
    token = reauth_mgr.issue_token()
    return {"reauth_token": token}


@admin_router.get("/integrations")
async def list_integrations(
    claims: TokenClaims = Depends(_require_auth),
    cred: CredentialClient = Depends(_get_credential_client),
) -> list[IntegrationListItem]:
    """List configured integrations via Credential Proxy.

    Args:
        claims: Authenticated token claims.
        cred: Credential Proxy client.

    Returns:
        List of integration entries (names and types only).
    """
    del claims
    items = await cred.list_integrations()
    return [_integration_list_item(item) for item in items]


@admin_router.post("/integrations", status_code=201)
async def create_integration(
    body: IntegrationCreateRequest,
    claims: TokenClaims = Depends(_require_auth),
    cred: CredentialClient = Depends(_get_credential_client),
) -> dict:
    """Create a new integration via Credential Proxy.

    Args:
        body: Integration creation request.
        claims: Authenticated token claims.
        cred: Credential Proxy client.

    Returns:
        Success message.
    """
    del claims
    await cred.create_integration(body.name, body.type, body.config)
    return _detail_response("Integration created")


@admin_router.delete("/integrations/{name}")
async def delete_integration(
    name: str,
    reauth_token: str = Depends(_require_reauth_token),
    claims: TokenClaims = Depends(_require_auth),
    reauth_mgr: ReauthManager = Depends(_get_reauth_manager),
    cred: CredentialClient = Depends(_get_credential_client),
) -> Response:
    """Delete an integration via Credential Proxy.

    Requires a valid re-authentication token.

    Args:
        name: Integration name to delete.
        reauth_token: Single-use reauth token from X-Reauth-Token header.
        request: FastAPI request.
        claims: Authenticated token claims.
        reauth_mgr: Re-authentication manager.
        cred: Credential Proxy client.
    """
    del claims
    _require_valid_reauth(reauth_mgr, reauth_token)
    await cred.delete_integration(name)
    return Response(status_code=204)


@admin_router.delete("/conversations/{conversation_id}")
async def delete_conversation_endpoint(
    conversation_id: str,
    reauth_token: str = Depends(_require_reauth_token),
    claims: TokenClaims = Depends(_require_auth),
    reauth_mgr: ReauthManager = Depends(_get_reauth_manager),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> Response:
    """Delete a conversation via DB Proxy Socket B.

    Requires a valid re-authentication token.

    Args:
        conversation_id: ID of conversation to delete.
        reauth_token: Single-use reauth token from X-Reauth-Token header.
        request: FastAPI request.
        claims: Authenticated token claims.
        reauth_mgr: Re-authentication manager.
        admin: DB Proxy Socket B client.
    """
    del claims
    _require_valid_reauth(reauth_mgr, reauth_token)
    await admin.delete_conversation(conversation_id)
    return Response(status_code=204)


@admin_router.get("/chat-bridges")
async def list_chat_bridge_links(
    claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
    provider: str | None = Query(default=None),
    conversation_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[ChatBridgeLinkItem]:
    """List stored external chat bridge links."""
    del claims
    items = await admin.list_chat_bridge_links(
        limit=limit,
        offset=offset,
        provider=provider,
        conversation_id=conversation_id,
    )
    return [_chat_bridge_link_item(item) for item in items]


@admin_router.get("/chat-bridge-providers")
async def list_chat_bridge_providers(
    claims: TokenClaims = Depends(_require_auth),
    chat_bridge_service: ChatBridgeService = Depends(_get_chat_bridge_service),
) -> list[ChatBridgeProviderItem]:
    """List configured external bridge providers available to the web UI."""
    del claims
    return [
        _chat_bridge_provider_item(
            provider,
            default_persona_user_id=(
                chat_bridge_service.default_persona_user_id_for_provider(provider)
            ),
        )
        for provider in chat_bridge_service.list_available_providers()
    ]


@admin_router.get("/chat-bridges/conversations/{conversation_id}")
async def get_chat_bridge_for_conversation(
    conversation_id: str,
    claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> ChatBridgeLinkItem | dict[str, None]:
    """Return the stored bridge link for one conversation, if it exists."""
    del claims
    bridge_link = await admin.get_chat_bridge_for_conversation(conversation_id)
    response_item = None if bridge_link is None else _chat_bridge_link_item(bridge_link)
    return _chat_bridge_for_conversation_response(response_item)


@admin_router.get("/chat-bridges/{bridge_link_id}/messages")
async def list_chat_bridge_message_links(
    bridge_link_id: str,
    claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    direction: str = Query(default=""),
) -> list[ChatBridgeMessageLinkItem]:
    """List stored bridge message mappings for one bridge link."""
    del claims
    items = await admin.list_chat_bridge_message_links(
        bridge_link_id=bridge_link_id,
        limit=limit,
        offset=offset,
        direction=direction or None,
    )
    return [_chat_bridge_message_link_item(item) for item in items]


@admin_router.post("/chat-bridges", status_code=201)
async def create_chat_bridge_link(
    body: ChatBridgeCreateRequest,
    reauth_token: str = Depends(_require_reauth_token),
    claims: TokenClaims = Depends(_require_auth),
    reauth_mgr: ReauthManager = Depends(_get_reauth_manager),
    chat_bridge_service: ChatBridgeService = Depends(_get_chat_bridge_service),
    orchestrator_client: OrchestratorClient = Depends(_get_orchestrator_client),
) -> ChatBridgeLinkItem:
    """Create one external chat bridge link from the web conversation side."""
    _require_valid_reauth(reauth_mgr, reauth_token)
    bridge_link = await chat_bridge_service.create_bridge(
        conversation_id=body.conversation_id,
        provider=BridgeProvider(body.provider),
        created_by=claims.jti,
        title=body.title or body.conversation_id,
        sync_direction=body.sync_direction,
        delivery_mode=body.delivery_mode,
        persona_mode=body.persona_mode,
        persona_user_id=body.persona_user_id,
    )
    response_item = _chat_bridge_link_item(bridge_link)
    await orchestrator_client.publish_conversation_event(
        conversation_id=response_item.conversation_id,
        event_type=StreamEventType.BRIDGE_LINK_UPDATED,
        data=_bridge_link_updated_event_data(response_item),
    )
    return response_item


@admin_router.post("/chat-bridges/{bridge_link_id}/disable")
async def disable_chat_bridge_link(
    bridge_link_id: str,
    reauth_token: str = Depends(_require_reauth_token),
    claims: TokenClaims = Depends(_require_auth),
    reauth_mgr: ReauthManager = Depends(_get_reauth_manager),
    admin: AdminDBClient = Depends(_get_admin_client),
    orchestrator_client: OrchestratorClient = Depends(_get_orchestrator_client),
) -> dict[str, object]:
    """Disable one stored external chat bridge link."""
    del claims
    _require_valid_reauth(reauth_mgr, reauth_token)
    updated = await admin.disable_chat_bridge_link(bridge_link_id)
    if not updated:
        return {"updated": False}
    bridge_link = await admin.get_chat_bridge_link(bridge_link_id)
    if bridge_link is None:
        return {"updated": True}
    response_item = _chat_bridge_link_item(bridge_link)
    await orchestrator_client.publish_conversation_event(
        conversation_id=response_item.conversation_id,
        event_type=StreamEventType.BRIDGE_LINK_UPDATED,
        data=_bridge_link_updated_event_data(response_item),
    )
    return {
        "updated": True,
        "chat_bridge_link": response_item.model_dump(),
    }


@admin_router.post("/chat-bridges/{bridge_link_id}/enable")
async def enable_chat_bridge_link(
    bridge_link_id: str,
    reauth_token: str = Depends(_require_reauth_token),
    claims: TokenClaims = Depends(_require_auth),
    reauth_mgr: ReauthManager = Depends(_get_reauth_manager),
    admin: AdminDBClient = Depends(_get_admin_client),
    orchestrator_client: OrchestratorClient = Depends(_get_orchestrator_client),
) -> dict[str, object]:
    """Enable one stored external chat bridge link."""
    del claims
    _require_valid_reauth(reauth_mgr, reauth_token)
    updated = await admin.enable_chat_bridge_link(bridge_link_id)
    if not updated:
        return {"updated": False}
    bridge_link = await admin.get_chat_bridge_link(bridge_link_id)
    if bridge_link is None:
        return {"updated": True}
    response_item = _chat_bridge_link_item(bridge_link)
    await orchestrator_client.publish_conversation_event(
        conversation_id=response_item.conversation_id,
        event_type=StreamEventType.BRIDGE_LINK_UPDATED,
        data=_bridge_link_updated_event_data(response_item),
    )
    return {
        "updated": True,
        "chat_bridge_link": response_item.model_dump(),
    }


@admin_router.put("/chat-bridges/{bridge_link_id}/settings")
async def update_chat_bridge_link_settings(
    bridge_link_id: str,
    body: ChatBridgeUpdateRequest,
    reauth_token: str = Depends(_require_reauth_token),
    claims: TokenClaims = Depends(_require_auth),
    reauth_mgr: ReauthManager = Depends(_get_reauth_manager),
    chat_bridge_service: ChatBridgeService = Depends(_get_chat_bridge_service),
    orchestrator_client: OrchestratorClient = Depends(_get_orchestrator_client),
) -> ChatBridgeLinkItem | dict[str, None]:
    """Update one stored external chat bridge link settings."""
    del claims
    _require_valid_reauth(reauth_mgr, reauth_token)
    bridge_link = await chat_bridge_service.update_bridge_settings(
        bridge_link_id=bridge_link_id,
        delivery_mode=body.delivery_mode,
        persona_mode=body.persona_mode,
        persona_user_id=body.persona_user_id,
    )
    if bridge_link is None:
        return {"chat_bridge_link": None}
    response_item = _chat_bridge_link_item(bridge_link)
    await orchestrator_client.publish_conversation_event(
        conversation_id=response_item.conversation_id,
        event_type=StreamEventType.BRIDGE_LINK_UPDATED,
        data=_bridge_link_updated_event_data(response_item),
    )
    return response_item


@admin_router.get("/profile")
async def get_user_profile(
    claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict:
    """Get user profile from DB Proxy.

    Args:
        claims: Authenticated token claims.
        admin: DB Proxy Socket B client.

    Returns:
        Dict with profile data.
    """
    del claims
    return await admin.get_user_profile()


@admin_router.put("/profile")
async def store_user_profile(
    body: UserProfileRequest,
    claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict:
    """Store user profile via DB Proxy.

    Args:
        body: Profile payload.
        claims: Authenticated token claims.
        admin: DB Proxy Socket B client.

    Returns:
        Success message.
    """
    del claims
    await admin.store_user_profile(body.model_dump())
    return _detail_response("Profile saved")


# --- Avatar upload/delete ---

ALLOWED_AVATAR_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_AVATAR_SIZE = 2 * 1024 * 1024  # 2 MB


@admin_router.put("/avatars/{avatar_type}")
async def upload_avatar(
    avatar_type: str,
    file: UploadFile,
    claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict:
    """Upload an avatar image.

    Args:
        avatar_type: Avatar type ("user" or "ai").
        file: Uploaded image file.
        claims: Authenticated token claims.
        admin: DB Proxy Socket B client.

    Returns:
        Success message with avatar URL.
    """
    del claims
    if avatar_type not in {"user", "ai"}:
        raise HTTPException(status_code=422, detail="Invalid avatar type")

    if not file.filename:
        raise HTTPException(status_code=422, detail="No filename provided")

    ext = _avatar_file_extension(file.filename)
    if ext not in ALLOWED_AVATAR_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail="Unsupported image format. Allowed: jpg, png, webp, gif",
        )

    content = await file.read()
    if len(content) > MAX_AVATAR_SIZE:
        raise HTTPException(
            status_code=422,
            detail="File too large. Maximum size: 2MB",
        )

    mime_type = (
        file.content_type
        or mimetypes.guess_type(file.filename)[0]
        or "application/octet-stream"
    )
    encoded = base64.b64encode(content).decode("ascii")
    await admin.store_avatar(avatar_type, encoded, mime_type)
    return {"url": f"/avatars/{avatar_type}"}


@admin_router.delete("/avatars/{avatar_type}")
async def delete_avatar(
    avatar_type: str,
    claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> Response:
    """Delete an avatar image.

    Args:
        avatar_type: Avatar type ("user" or "ai").
        claims: Authenticated user.
        admin: DB Proxy client.
    """
    del claims
    if avatar_type not in {"user", "ai"}:
        raise HTTPException(status_code=422, detail="Invalid avatar type")
    await admin.delete_avatar(avatar_type)
    return Response(status_code=204)


def _avatar_file_extension(filename: str) -> str:
    """Extract lowercase file extension from filename.

    Args:
        filename: Original filename.

    Returns:
        Lowercase extension including the leading dot.
    """
    dot_index = filename.rfind(".")
    if dot_index == -1:
        return ""
    return filename[dot_index:].lower()


@admin_router.get("/skills")
async def list_skills(
    request: Request,
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict[str, list[dict]]:
    """List all saved skills.

    Args:
        request: Incoming request, used for SPA fallback and auth.
        admin: DB Proxy Socket B client.

    Returns:
        List of skills with name, title, description, use_count.
    """
    spa_response = _spa_index_response(request)
    if spa_response is not None:
        return spa_response
    _require_request_auth(request)
    skills = await admin.list_skills()
    return {"skills": skills}


@admin_router.delete("/skills/{name}")
async def delete_skill(
    name: str,
    claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> Response:
    """Delete a skill by name.

    Args:
        name: Skill name to delete.
        claims: Authenticated user.
        admin: DB Proxy client.
    """
    del claims
    await admin.delete_skill(name)
    return Response(status_code=204)


@admin_router.put("/skills/{name}")
async def update_skill(
    name: str,
    body: dict,
    claims: TokenClaims = Depends(_require_auth),
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict[str, str]:
    """Update a skill by name.

    Args:
        name: Skill name to update.
        body: Partial update fields (title, description, content, enabled).
        claims: Authenticated token claims.
        admin: DB Proxy Socket B client.

    Returns:
        Confirmation message.
    """
    del claims
    await admin.update_skill(
        name=name,
        title=body.get("title"),
        description=body.get("description"),
        trigger=body.get("trigger"),
        procedure=body.get("procedure"),
        errors=body.get("errors"),
        verification=body.get("verification"),
        enabled=body.get("enabled"),
    )
    return _detail_response(f"Skill '{name}' updated")


@admin_router.get("/skills/{name}")
async def get_skill(
    name: str,
    request: Request,
    admin: AdminDBClient = Depends(_get_admin_client),
) -> dict:
    """Get a single skill with full data and usage log.

    Args:
        name: Skill name.
        request: Incoming request, used for SPA fallback and auth.
        admin: DB Proxy Socket B client.

    Returns:
        Skill data with usage logs.
    """
    spa_response = _spa_index_response(request)
    if spa_response is not None:
        return spa_response
    _require_request_auth(request)
    skill = await admin.get_skill(name)
    if skill is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Skill not found")
    logs = await admin.list_skill_usage_logs(skill.get("id", ""))
    return {"skill": skill, "logs": logs}
