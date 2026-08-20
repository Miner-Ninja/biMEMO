"""Minimal HTTP startup gate for password-first manual unlock."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from backend.models.requests import PasswordAuthRequest  # noqa: TC001
from backend.security.startup_state import StartupState


if TYPE_CHECKING:
    import uvicorn

    from backend.config import Settings


# --- Startup gate response defaults ---
MIN_STARTUP_PASSWORD_LENGTH = 8
STARTUP_GATE_SHUTDOWN_DELAY_SECONDS = 0.1
SETUP_STATE_DETAILS = "Application setup password is required"
LOCKED_STATE_DETAILS = "Application unlock password is required"
AUTO_UNLOCK_FAILED_DETAILS = "Automatic unlock failed; manual password is required"


def _startup_state_detail(state: StartupState) -> str:
    """Return a public status detail for one startup state."""
    if state == StartupState.SETUP:
        return SETUP_STATE_DETAILS
    if state == StartupState.AUTO_UNLOCK_FAILED:
        return AUTO_UNLOCK_FAILED_DETAILS
    return LOCKED_STATE_DETAILS


def _needs_setup(state: StartupState) -> bool:
    """Return whether the startup gate is waiting for first setup."""
    return state == StartupState.SETUP


def _status_payload(state: StartupState) -> dict[str, object]:
    """Build the startup gate auth/status response body."""
    return {
        "startup_state": state.value,
        "needs_setup": _needs_setup(state),
        "authenticated": False,
        "ready": False,
        "detail": _startup_state_detail(state),
    }


def _health_payload(state: StartupState) -> dict[str, object]:
    """Build the startup gate health response body."""
    return {
        "status": state.value,
        "ready": False,
        "detail": _startup_state_detail(state),
    }


def _validate_password(password: str) -> None:
    """Validate one submitted startup password."""
    if len(password) < MIN_STARTUP_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 8 characters",
        )


async def _stop_server_after_response(server: uvicorn.Server) -> None:
    """Ask uvicorn to stop after the current response has been sent."""
    await asyncio.sleep(STARTUP_GATE_SHUTDOWN_DELAY_SECONDS)
    server.should_exit = True


async def _accept_startup_password(
    *,
    password: str,
    password_future: asyncio.Future[str],
    server: uvicorn.Server,
) -> dict[str, str]:
    """Store one submitted password in memory and stop the startup gate."""
    if password_future.done():
        return {"status": "starting"}
    _validate_password(password)
    password_future.set_result(password)
    server.startup_gate_stop_task = asyncio.create_task(
        _stop_server_after_response(server)
    )
    return {"status": "starting"}


def _server_from_request(request: Request) -> object:
    """Return the uvicorn server object stored on app state."""
    return request.app.state.startup_gate_server


def create_startup_gate_app(
    *,
    state: StartupState,
    password_future: asyncio.Future[str],
) -> FastAPI:
    """Create the minimal HTTP app used before database unlock.

    Args:
        state: Visible startup state served by this gate.
        password_future: Future completed with the submitted startup password.

    Returns:
        FastAPI application with health, status, setup, and unlock endpoints.
    """
    app = FastAPI(title="biMEMO Startup Gate", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> JSONResponse:
        """Return startup-gate health as 503 until unlock is complete."""
        return JSONResponse(content=_health_payload(state), status_code=503)

    @app.get("/auth/status")
    async def auth_status() -> dict[str, object]:
        """Return startup-gate authentication state."""
        return _status_payload(state)

    @app.post("/auth/setup")
    async def auth_setup(
        body: PasswordAuthRequest,
        request: Request,
    ) -> dict[str, str]:
        """Accept the first setup password when no encrypted key exists."""
        if state != StartupState.SETUP:
            raise HTTPException(
                status_code=409,
                detail="Application is not in setup state",
            )
        return await _accept_startup_password(
            password=body.password,
            password_future=password_future,
            server=_server_from_request(request),
        )

    @app.post("/auth/login")
    async def auth_login(
        body: PasswordAuthRequest,
        request: Request,
    ) -> dict[str, str]:
        """Accept the unlock password when the encrypted key already exists."""
        if state not in {StartupState.LOCKED, StartupState.AUTO_UNLOCK_FAILED}:
            raise HTTPException(
                status_code=409,
                detail="Application is not in locked state",
            )
        return await _accept_startup_password(
            password=body.password,
            password_future=password_future,
            server=_server_from_request(request),
        )

    return app


async def run_startup_gate(settings: Settings, state: StartupState) -> str:
    """Run the startup gate until a password is submitted.

    Args:
        settings: Application settings containing host and port.
        state: Startup state to expose through the gate.

    Returns:
        Password submitted by the operator.
    """
    import uvicorn

    password_future: asyncio.Future[str] = asyncio.Future()
    app = create_startup_gate_app(
        state=state,
        password_future=password_future,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=settings.host,
            port=settings.port,
            log_config=None,
        )
    )
    app.state.startup_gate_server = server
    server_task = asyncio.create_task(server.serve())
    password = await password_future
    await server_task
    return password
