"""Re-export hub for all IPC client classes.

Provides backward-compatible imports for OrchestratorClient,
AdminDBClient, SigningClient, CredentialClient, and _BaseIPCClient.
Also re-exports the IPC response type maps used during connection
setup. All actual implementations live in dedicated modules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.ipc.models import DBResult


if TYPE_CHECKING:
    from pydantic import BaseModel
from backend.processes.fastapi_app.admin_db_client import AdminDBClient
from backend.processes.fastapi_app.base_client import _BaseIPCClient
from backend.processes.fastapi_app.credential_client import CredentialClient
from backend.processes.fastapi_app.orchestrator_client import (
    ORCHESTRATOR_RESPONSE_TYPES,
    OrchestratorClient,
)
from backend.processes.fastapi_app.signing_client import SigningClient


# IPC type map for incoming DB proxy messages
DB_PROXY_RESPONSE_TYPES: dict[str, type[BaseModel]] = {
    "DBResult": DBResult,
}

__all__ = [
    "DB_PROXY_RESPONSE_TYPES",
    "ORCHESTRATOR_RESPONSE_TYPES",
    "AdminDBClient",
    "CredentialClient",
    "OrchestratorClient",
    "SigningClient",
    "_BaseIPCClient",
]
