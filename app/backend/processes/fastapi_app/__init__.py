"""FastAPI App subpackage — re-exports all public symbols."""

from .clients import (
    DB_PROXY_RESPONSE_TYPES,
    ORCHESTRATOR_RESPONSE_TYPES,
    AdminDBClient,
    CredentialClient,
    OrchestratorClient,
    SigningClient,
    _BaseIPCClient,
)
from .deps import (
    _csrf_tokens,
    _get_admin_client,
    _get_audit_fn,
    _get_credential_client,
    _get_csrf_token_for_jti,
    _get_orchestrator_client,
    _get_reauth_manager,
    _get_session_manager,
    _get_signing_client,
    _get_sse_manager,
    _require_auth,
)
from .main import (
    FRONTEND_DIST,
    create_app,
    fastapi_main,
)
from .middleware import (
    _cookie_secure,
    _set_auth_cookies,
)
from .routes import (
    admin_router,
    auth_router,
    chat_router,
    health_router,
    llm_router,
)
from .routes.chat_stream import SSE_KEEPALIVE_INTERVAL


__all__ = [
    "ADMIN_ROUTER",
    "AUTH_ROUTER",
    "CHAT_ROUTER",
    "DB_PROXY_RESPONSE_TYPES",
    "FRONTEND_DIST",
    "HEALTH_ROUTER",
    "LLM_ROUTER",
    "ORCHESTRATOR_RESPONSE_TYPES",
    "SSE_KEEPALIVE_INTERVAL",
    "AdminDBClient",
    "CredentialClient",
    "OrchestratorClient",
    "SigningClient",
    "_BaseIPCClient",
    "_cookie_secure",
    "_csrf_tokens",
    "_get_admin_client",
    "_get_audit_fn",
    "_get_credential_client",
    "_get_csrf_token_for_jti",
    "_get_orchestrator_client",
    "_get_reauth_manager",
    "_get_session_manager",
    "_get_signing_client",
    "_get_sse_manager",
    "_require_auth",
    "_set_auth_cookies",
    "admin_router",
    "auth_router",
    "chat_router",
    "create_app",
    "fastapi_main",
    "health_router",
    "llm_router",
]
