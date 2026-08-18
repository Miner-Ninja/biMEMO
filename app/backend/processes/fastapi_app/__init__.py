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
    _get_realtime_manager,
    _get_reauth_manager,
    _get_session_manager,
    _get_signing_client,
    _require_auth,
)

# ``main`` is NOT imported here (see ``__getattr__`` below): it imports
# the ASGI middleware package, which in turn imports ``problem`` from
# this package. Importing it eagerly would create a circular import
# whenever the middleware is imported first.
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


_MAIN_EXPORTS = ("FRONTEND_DIST", "create_app", "fastapi_main")


def __getattr__(name: str) -> object:
    """Import ``main`` lazily for its re-exported symbols.

    Keeps ``from backend.processes.fastapi_app import create_app``
    working while keeping the package import lightweight enough for
    the middleware package to import ``problem`` from this package.
    """
    if name in _MAIN_EXPORTS:
        from . import main

        return getattr(main, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ADMIN_ROUTER",
    "AUTH_ROUTER",
    "CHAT_ROUTER",
    "DB_PROXY_RESPONSE_TYPES",
    "FRONTEND_DIST",
    "HEALTH_ROUTER",
    "LLM_ROUTER",
    "ORCHESTRATOR_RESPONSE_TYPES",
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
    "_get_realtime_manager",
    "_get_reauth_manager",
    "_get_session_manager",
    "_get_signing_client",
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
