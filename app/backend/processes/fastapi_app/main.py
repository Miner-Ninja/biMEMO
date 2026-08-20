"""FastAPI application factory and process entry point."""

import logging
import os
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from starlette.routing import Mount

from backend.channels.endpoints import EndpointRateLimiter, ReauthManager
from backend.channels.jwt_auth import SessionManager
from backend.channels.realtime import RealtimeConnectionManager
from backend.chat_bridge import (
    ChatBridgeService,
)
from backend.chat_bridge.providers import build_configured_providers
from backend.config import get_settings
from backend.ipc.ipc_channel import IPCSocketClient
from backend.middleware.body_size import BodySizeMiddleware
from backend.middleware.cache_control import CacheControlMiddleware
from backend.middleware.csrf import CsrfMiddleware
from backend.middleware.jwt_auth import JwtAuthMiddleware
from backend.middleware.rate_limit import RateLimitMiddleware
from backend.middleware.security_headers import SecurityHeadersMiddleware

from .clients import (
    DB_PROXY_RESPONSE_TYPES,
    ORCHESTRATOR_RESPONSE_TYPES,
    AdminDBClient,
    CredentialClient,
    OrchestratorClient,
    SigningClient,
)
from .deps import _csrf_tokens, _get_csrf_token_for_jti
from .problem import (
    SLUG_NOT_FOUND,
    problem_response,
    register_problem_handlers,
)
from .routes import (
    admin_router,
    agents_router,
    auth_router,
    avatars_router,
    chat_bridge_router,
    chat_router,
    files_router,
    health_router,
    llm_router,
    memory_router,
    scheduled_tasks_router,
    tools_catalog_router,
    trace_router,
    tts_router,
    workspace_router,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from starlette.exceptions import HTTPException

    from backend.config import Settings
    from backend.llm.tts_client import TTSClient


logger = logging.getLogger(__name__)

FRONTEND_DIST = Path(os.environ.get("PYTHONPATH", "/app")) / "frontend" / "dist"


def _single_configured_id(configured_ids: frozenset[str]) -> str:
    """Return the only configured id when the set is unambiguous."""
    if len(configured_ids) != 1:
        return ""
    return next(iter(configured_ids))


async def _connect_ipc_client(
    *,
    socket_path: str,
    secret: bytes,
    type_map: dict[str, type],
) -> IPCSocketClient:
    """Create and connect one IPC client."""
    ipc_client = IPCSocketClient(
        socket_path=socket_path,
        secret=secret,
        type_map=type_map,
    )
    await ipc_client.connect()
    return ipc_client


async def _connect_optional_ipc_client(
    *,
    socket_path: str,
    secret: bytes,
    type_map: dict[str, type],
) -> IPCSocketClient | None:
    """Create and connect one optional IPC client when a socket path is set."""
    if not socket_path:
        return None
    return await _connect_ipc_client(
        socket_path=socket_path,
        secret=secret,
        type_map=type_map,
    )


def _build_chat_bridge_service(
    *,
    settings: Settings,
    admin_client: AdminDBClient,
) -> ChatBridgeService | None:
    """Create the chat bridge service from registered providers."""
    providers = build_configured_providers(settings)
    if not providers:
        return None
    from backend.chat_bridge.providers import REGISTERED_PROVIDERS

    all_persona_ids: set[str] = set()
    for provider_class in REGISTERED_PROVIDERS.values():
        all_persona_ids.update(
            provider_class.allowed_inbound_user_ids(settings),
        )
    return ChatBridgeService(
        admin_client=admin_client,
        providers=providers,
        allowed_persona_user_ids=frozenset(all_persona_ids),
    )


def _build_audit_fn(admin_client: AdminDBClient) -> callable:
    """Build one async audit writer wrapper for middleware and routes."""

    async def _audit_fn(
        event_type: str,
        tool_name: str | None = None,
        parameters: str | None = None,
        result: str | None = None,
        zone: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        """Write one audit event through the shared admin client."""
        await admin_client.write_audit(
            event_type=event_type,
            tool_name=tool_name,
            parameters=parameters,
            result=result,
            zone=zone,
            duration_ms=duration_ms,
        )

    return _audit_fn


def _apply_audit_fn_to_middleware(
    application: FastAPI,
    *,
    sign_client: SigningClient | None,
    audit_fn: callable,
) -> None:
    """Inject signing and audit callbacks into middleware instances."""
    walker = application.middleware_stack
    while walker is not None:
        if isinstance(walker, JwtAuthMiddleware):
            walker.signing_client = sign_client
            walker.audit_fn = audit_fn
        elif isinstance(walker, CsrfMiddleware | RateLimitMiddleware):
            walker.audit_fn = audit_fn
        walker = getattr(walker, "app", None)


def _build_tts_client(settings: Settings) -> TTSClient | None:
    """Create the TTS client when TTS settings are configured."""
    if not settings.tts_endpoint:
        return None
    from backend.llm.tts_client import TTSClient

    logger.info("TTS client initialized: %s", settings.tts_endpoint)
    return TTSClient(
        endpoint=settings.tts_endpoint,
        voice_id=settings.tts_voice_id,
    )


def _register_api_routes(application: FastAPI) -> None:
    """Register all API routers on the FastAPI application."""
    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(chat_router)
    application.include_router(chat_bridge_router)
    application.include_router(admin_router)
    application.include_router(agents_router)
    application.include_router(memory_router)
    application.include_router(trace_router)
    application.include_router(llm_router)
    application.include_router(files_router)
    application.include_router(avatars_router)
    application.include_router(scheduled_tasks_router)
    application.include_router(tools_catalog_router)
    application.include_router(tts_router)
    application.include_router(workspace_router)


async def _close_optional_ipc_client(ipc_client: IPCSocketClient | None) -> None:
    """Close one optional IPC client when it exists."""
    if ipc_client is not None:
        await ipc_client.close()


def create_app(
    orchestrator_socket: str,
    orchestrator_secret: bytes,
    db_proxy_socket_b: str,
    db_proxy_secret_b: bytes,
    credential_socket: str = "",
    credential_secret: bytes = b"",
    signing_socket: str = "",
    signing_secret: bytes = b"",
) -> FastAPI:
    """Create the FastAPI application for multi-process mode.

    Configures IPC lifespan, registers routes, and adds
    middleware (no StateCheckMiddleware since the app is
    always READY).

    Args:
        orchestrator_socket: Path to Orchestrator IPC socket.
        orchestrator_secret: HMAC secret for Orchestrator channel.
        db_proxy_socket_b: Path to DB Proxy Socket B.
        db_proxy_secret_b: HMAC secret for Socket B channel.
        credential_socket: Path to Credential Proxy IPC socket.
        credential_secret: HMAC secret for Credential channel.
        signing_socket: Path to Signing Proxy IPC socket.
        signing_secret: HMAC secret for Signing channel.

    Returns:
        Configured FastAPI application.
    """
    session_manager = SessionManager()
    realtime_manager = RealtimeConnectionManager()
    reauth_manager = ReauthManager()
    rate_limiter = EndpointRateLimiter()

    @asynccontextmanager
    async def _lifespan(
        application: FastAPI,
    ) -> AsyncIterator[None]:
        """Connect IPC channels and receive signing key."""
        settings = get_settings()
        application.state.settings = settings

        db_ipc = await _connect_ipc_client(
            socket_path=db_proxy_socket_b,
            secret=db_proxy_secret_b,
            type_map=DB_PROXY_RESPONSE_TYPES,
        )
        admin_client = AdminDBClient(db_ipc)
        application.state.admin_client = admin_client
        application.state.chat_bridge_service = _build_chat_bridge_service(
            settings=settings,
            admin_client=admin_client,
        )

        sign_ipc = await _connect_optional_ipc_client(
            socket_path=signing_socket,
            secret=signing_secret,
            type_map=DB_PROXY_RESPONSE_TYPES,
        )
        sign_client = SigningClient(sign_ipc) if sign_ipc else None
        application.state.signing_client = sign_client

        cred_ipc = await _connect_optional_ipc_client(
            socket_path=credential_socket,
            secret=credential_secret,
            type_map=DB_PROXY_RESPONSE_TYPES,
        )
        cred_client = CredentialClient(cred_ipc) if cred_ipc else None
        application.state.credential_client = cred_client

        orch_ipc = await _connect_ipc_client(
            socket_path=orchestrator_socket,
            secret=orchestrator_secret,
            type_map=ORCHESTRATOR_RESPONSE_TYPES,
        )
        orch_client = OrchestratorClient(orch_ipc)
        await orch_client.start()
        application.state.orchestrator_client = orch_client

        _audit_fn = _build_audit_fn(admin_client)
        application.state.audit_fn = _audit_fn

        _apply_audit_fn_to_middleware(
            application,
            sign_client=sign_client,
            audit_fn=_audit_fn,
        )
        application.state.tts_client = _build_tts_client(settings)

        async with AsyncExitStack() as lifespan_stack:
            # MCP server setup (when enabled). The FastMCP app carries its own
            # lifespan that starts the Streamable HTTP session manager; it must
            # be entered here, or every /mcp request fails with an
            # uninitialized-task-group error.
            if settings.mcp_enabled:
                if cred_client is None:
                    msg = (
                        "MCP is enabled but credential proxy is not available. "
                        "Cannot start MCP server."
                    )
                    raise RuntimeError(msg)

                # Seed agent tokens from environment variables
                from backend.processes.fastapi_app.routes.mcp import (
                    build_mcp_server,
                    seed_mcp_tokens_from_env,
                )

                await seed_mcp_tokens_from_env(cred_client)
                mcp_server = build_mcp_server(admin_client, orch_client, cred_client)
                mcp_app = mcp_server.http_app(path="/")
                # Insert at the front so /mcp is matched before the
                # StaticFiles("/") SPA catch-all; Starlette matches routes in
                # order, and this mount is added after create_app's routes.
                application.router.routes.insert(0, Mount("/mcp", app=mcp_app))
                await lifespan_stack.enter_async_context(mcp_app.lifespan(mcp_app))
                logger.info("MCP endpoint mounted at /mcp")

            logger.info("FastAPI process ready (IPC connected)")

            yield

            # Shutdown: close IPC connections
            logger.info("FastAPI process shutting down")
            await orch_client.stop()
            await _close_optional_ipc_client(cred_ipc)
            await _close_optional_ipc_client(sign_ipc)
            await db_ipc.close()

    application = FastAPI(
        title="biMEMO",
        docs_url=None,
        redoc_url=None,
        lifespan=_lifespan,
    )
    register_problem_handlers(application)

    # Store managers on app.state
    application.state.session_manager = session_manager
    application.state.realtime_manager = realtime_manager
    application.state.reauth_manager = reauth_manager
    application.state.rate_limiter = rate_limiter
    application.state.csrf_tokens = _csrf_tokens
    application.state.signing_client = None
    application.state.chat_bridge_service = None
    application.state.audit_fn = None

    # Register routes (before static mount)
    _register_api_routes(application)

    # Middleware (reverse order: last added = outermost)
    # 5. Rate limiting (innermost)
    application.add_middleware(
        RateLimitMiddleware,
        limiter=rate_limiter,
    )
    # 4. CSRF validation
    application.add_middleware(
        CsrfMiddleware,
        get_expected_token=_get_csrf_token_for_jti,
    )
    # 3. JWT authentication (signing_client set in lifespan)
    application.add_middleware(
        JwtAuthMiddleware,
        session_manager=session_manager,
    )
    # NO StateCheckMiddleware — app is always READY
    # 2. Request body size limit
    application.add_middleware(BodySizeMiddleware)
    # 1. Security headers
    application.add_middleware(SecurityHeadersMiddleware)
    # 0. Cache-control policy (outermost; setdefault-style: a Cache-Control
    # already set by a route is never overwritten)
    application.add_middleware(CacheControlMiddleware)

    # Static files
    if FRONTEND_DIST.is_dir():
        application.mount(
            "/",
            StaticFiles(directory=FRONTEND_DIST, html=True),
            name="frontend",
        )

        index_html = FRONTEND_DIST / "index.html"

        @application.exception_handler(404)
        async def _spa_fallback(request: Request, exc: HTTPException) -> Response:
            """Serve the frontend index for HTML paths that miss FastAPI routes."""
            accept = request.headers.get("accept", "")
            if "text/html" in accept:
                from fastapi.responses import FileResponse

                return FileResponse(
                    index_html,
                    headers={"Cache-Control": "no-cache"},
                )
            # Only the static mount carries a "path" path parameter, so
            # its presence tells an unknown path apart from a 404 raised
            # by a real route (which keeps its own detail text).
            path_params = request.scope.get("path_params") or {}
            if "path" in path_params:
                detail = "Not Found"
            else:
                route_detail = getattr(exc, "detail", None)
                detail = route_detail if isinstance(route_detail, str) else "Not Found"
            return problem_response(
                status=404,
                type_slug=SLUG_NOT_FOUND,
                detail=detail,
            )

    return application


def fastapi_main(
    orchestrator_socket: str,
    orchestrator_secret: bytes,
    db_proxy_socket_b: str,
    db_proxy_secret_b: bytes,
    credential_socket: str = "",
    credential_secret: bytes = b"",
    signing_socket: str = "",
    signing_secret: bytes = b"",
) -> None:
    """Entry point for the FastAPI process.

    Called by ProcessSupervisor. Creates the FastAPI app
    with IPC connections and starts uvicorn.

    Args:
        orchestrator_socket: Path to Orchestrator IPC socket.
        orchestrator_secret: HMAC secret for Orchestrator channel.
        db_proxy_socket_b: Path to DB Proxy Socket B.
        db_proxy_secret_b: HMAC secret for Socket B channel.
        credential_socket: Path to Credential Proxy IPC socket.
        credential_secret: HMAC secret for Credential channel.
        signing_socket: Path to Signing Proxy IPC socket.
        signing_secret: HMAC secret for Signing channel.
    """
    import uvicorn

    app = create_app(
        orchestrator_socket=orchestrator_socket,
        orchestrator_secret=orchestrator_secret,
        db_proxy_socket_b=db_proxy_socket_b,
        db_proxy_secret_b=db_proxy_secret_b,
        credential_socket=credential_socket,
        credential_secret=credential_secret,
        signing_socket=signing_socket,
        signing_secret=signing_secret,
    )

    settings = get_settings()
    uvicorn.run(app, host=settings.host, port=settings.port)
