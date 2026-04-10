"""Health check route."""

from fastapi import APIRouter, Request


health_router = APIRouter()


def _orchestrator_status(request: Request) -> str:
    """Return the visible orchestrator connectivity status for health output."""
    orch = getattr(request.app.state, "orchestrator_client", None)
    if orch and orch.is_connected:
        return "connected"
    return "disconnected"


def _health_payload(request: Request) -> dict[str, str]:
    """Build the standard health response body."""
    return {
        "status": "ok",
        "orchestrator": _orchestrator_status(request),
    }


@health_router.get("/health")
async def health(request: Request) -> dict:
    """Return health check response.

    Always returns status ok since the app is always READY
    in multi-process mode (no SETUP/LOCKED states).

    Args:
        request: FastAPI request.

    Returns:
        Health status dict.
    """
    return _health_payload(request)
