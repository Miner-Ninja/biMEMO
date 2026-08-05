"""Integration, credential, and LLM-config DB proxy handler factories."""

import json
import uuid
from typing import TYPE_CHECKING, Any

from ..common import LLM_SERVICE_TYPES, OperationHandler, validate_params
from ..params import (
    ActivateLLMConfigParams,
    CreateIntegrationParams,
    CreateLLMConfigParams,
    DeleteCredentialParams,
    DeleteIntegrationParams,
    DeleteLLMConfigParams,
    GetCredentialParams,
    GetIntegrationConfigParams,
    GetLLMConfigFullParams,
    StoreCredentialParams,
    StoreLLMConfigParams,
    UpdateLLMConfigParams,
)


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager


DEACTIVATE_LLM_CONFIGS_SQL = (
    "UPDATE integrations SET enabled = 0 WHERE service_type IN (?, ?, ?)"
)
SELECT_ACTIVE_LLM_META_SQL = (
    "SELECT id, service_type FROM integrations "
    "WHERE service_type IN ('openrouter', 'ollama', 'custom') "
    "AND enabled = 1 LIMIT 1"
)
SELECT_LLM_CONFIGS_SQL = (
    "SELECT id, service_type, enabled, created_at FROM integrations "
    "WHERE service_type IN ('openrouter', 'ollama', 'custom') "
    "ORDER BY created_at DESC"
)


def _new_id() -> str:
    """Return one new random identifier for stored rows."""
    return str(uuid.uuid4())


def _store_credentials(
    db: DatabaseManager,
    *,
    integration_id: str,
    credentials: dict[str, str],
) -> None:
    """Insert all credential rows for one integration."""
    for cred_type, value in credentials.items():
        db.execute(
            "INSERT INTO credentials (id, integration_id, credential_type, value) "
            "VALUES (?, ?, ?, ?)",
            (_new_id(), integration_id, cred_type, value),
        )


def _credential_values(
    db: DatabaseManager,
    *,
    integration_id: str,
) -> dict[str, str]:
    """Load all credential values for one integration."""
    return dict(
        db.execute(
            "SELECT credential_type, value FROM credentials WHERE integration_id = ?",
            (integration_id,),
        ).fetchall()
    )


def _integration_exists(db: DatabaseManager, *, integration_id: str) -> bool:
    """Return whether one integration row exists."""
    row = db.execute(
        "SELECT id FROM integrations WHERE id = ?",
        (integration_id,),
    ).fetchone()
    return row is not None


def _not_found_result() -> dict[str, str]:
    """Return the standard not-found payload for LLM config operations."""
    return {"error": "not_found"}


def _integration_list_entry(row: tuple[object, ...]) -> dict[str, Any]:
    """Convert one integration row into a public list entry."""
    import json as _json

    raw_config = row[5]
    config = _json.loads(raw_config) if raw_config else {}
    return {
        "id": row[0],
        "name": row[1],
        "service_type": row[2],
        "enabled": bool(row[3]),
        "config": config,
        "created_at": row[4],
    }


def _llm_config_entry(
    row: tuple[object, ...],
    credentials: dict[str, str],
) -> dict[str, Any]:
    """Convert one integration row plus credentials into an LLM config entry."""
    integration_id, service_type, enabled, created_at = row
    entry: dict[str, Any] = {
        "id": integration_id,
        "service_type": service_type,
        "enabled": bool(enabled),
        "created_at": created_at,
        "has_api_key": "api_key" in credentials,
    }
    for cred_type, value in credentials.items():
        if cred_type != "api_key":
            entry[cred_type] = value
    return entry


def _llm_meta(credentials: dict[str, str], *, service_type: str) -> dict[str, Any]:
    """Build one safe LLM metadata response without secrets."""
    meta: dict[str, Any] = {
        "configured": True,
        "service_type": service_type,
    }
    for cred_type in (
        "model",
        "dialog_recorder_model",
        "base_url",
        "dialog_recorder_base_url",
        "num_ctx",
    ):
        value = credentials.get(cred_type)
        if value is not None:
            meta[cred_type] = value
    return meta


def make_store_credential_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for store_credential operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Store a credential for an integration."""
        p = validate_params(params, StoreCredentialParams)
        credential_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO credentials "
            "(id, integration_id, credential_type, value) "
            "VALUES (?, ?, ?, ?)",
            (credential_id, p.integration_id, p.credential_type, p.value),
        )
        db.commit()
        return {"credential_id": credential_id}

    return handle


def make_get_credential_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for get_credential operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Get a credential value by integration and type."""
        p = validate_params(params, GetCredentialParams)
        row = db.execute(
            "SELECT value FROM credentials "
            "WHERE integration_id = ? AND credential_type = ?",
            (p.integration_id, p.credential_type),
        ).fetchone()
        if row is None:
            return {"value": None}
        return {"value": row[0]}

    return handle


def make_delete_credential_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for delete_credential operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Delete a credential by ID."""
        p = validate_params(params, DeleteCredentialParams)
        cursor = db.execute("DELETE FROM credentials WHERE id = ?", (p.credential_id,))
        db.commit()
        return {"deleted": cursor.rowcount > 0}

    return handle


def make_create_integration_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for create_integration operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Create a new integration entry."""
        p = validate_params(params, CreateIntegrationParams)
        integration_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO integrations (id, name, service_type, config) "
            "VALUES (?, ?, ?, ?)",
            (integration_id, p.name, p.service_type, json.dumps(p.config)),
        )
        db.commit()
        return {"integration_id": integration_id}

    return handle


def make_list_integrations_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for list_integrations operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """List all integrations."""
        rows = db.execute(
            "SELECT id, name, service_type, enabled, created_at, config "
            "FROM integrations ORDER BY created_at DESC",
        ).fetchall()
        return {"integrations": [_integration_list_entry(row) for row in rows]}

    return handle


def make_delete_integration_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for delete_integration operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Delete an integration by name."""
        p = validate_params(params, DeleteIntegrationParams)
        cursor = db.execute("DELETE FROM integrations WHERE name = ?", (p.name,))
        db.commit()
        return {"deleted": cursor.rowcount > 0}

    return handle


def make_get_integration_config_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for get_integration_config operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Fetch the active integration and its credentials for a service type."""
        p = validate_params(params, GetIntegrationConfigParams)
        row = db.execute(
            "SELECT id, name, service_type, enabled FROM integrations "
            "WHERE service_type = ? AND enabled = 1 LIMIT 1",
            (p.service_type,),
        ).fetchone()
        if row is None:
            return {"integration": None, "credentials": {}}
        integration_id, name, service_type, enabled = row
        credentials = _credential_values(db, integration_id=integration_id)
        return {
            "integration": {
                "id": integration_id,
                "name": name,
                "service_type": service_type,
                "enabled": bool(enabled),
            },
            "credentials": credentials,
        }

    return handle


def make_store_llm_config_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for store_llm_config operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Upsert an active LLM provider integration and its credentials."""
        p = validate_params(params, StoreLLMConfigParams)
        old_row = db.execute(
            "SELECT id FROM integrations "
            "WHERE service_type = ? AND enabled = 1 LIMIT 1",
            (p.service_type,),
        ).fetchone()
        existing_creds = (
            _credential_values(db, integration_id=old_row[0])
            if old_row is not None
            else {}
        )
        merged = {**existing_creds, **p.credentials}
        db.execute(
            "UPDATE integrations SET enabled = 0 WHERE service_type = ?",
            (p.service_type,),
        )
        integration_id = _new_id()
        db.execute(
            "INSERT INTO integrations (id, name, service_type, enabled, config) "
            "VALUES (?, ?, ?, 1, ?)",
            (integration_id, p.service_type, p.service_type, json.dumps({})),
        )
        _store_credentials(db, integration_id=integration_id, credentials=merged)
        db.commit()
        return {"integration_id": integration_id}

    return handle


def make_get_llm_config_meta_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for get_llm_config_meta operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Return enabled LLM metadata without secrets."""
        row = db.execute(SELECT_ACTIVE_LLM_META_SQL).fetchone()
        if row is None:
            return {"configured": False}
        integration_id, service_type = row
        credentials = _credential_values(db, integration_id=integration_id)
        return _llm_meta(credentials, service_type=service_type)

    return handle


def make_list_llm_configs_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for list_llm_configs operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Return all saved LLM configurations with safe metadata."""
        rows = db.execute(SELECT_LLM_CONFIGS_SQL).fetchall()
        configs = [
            _llm_config_entry(
                row,
                _credential_values(db, integration_id=str(row[0])),
            )
            for row in rows
        ]
        return {"configs": configs}

    return handle


def make_create_llm_config_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for create_llm_config operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Create a new inactive LLM configuration."""
        p = validate_params(params, CreateLLMConfigParams)
        integration_id = _new_id()
        db.execute(
            "INSERT INTO integrations (id, name, service_type, enabled, config) "
            "VALUES (?, ?, ?, 0, ?)",
            (integration_id, p.service_type, p.service_type, json.dumps({})),
        )
        _store_credentials(
            db,
            integration_id=integration_id,
            credentials=p.credentials,
        )
        db.commit()
        return {"id": integration_id}

    return handle


def make_update_llm_config_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for update_llm_config operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Update an existing LLM configuration with merged credentials."""
        p = validate_params(params, UpdateLLMConfigParams)
        if not _integration_exists(db, integration_id=p.config_id):
            return _not_found_result()
        existing = _credential_values(db, integration_id=p.config_id)
        merged = {**existing, **p.credentials}
        db.execute("DELETE FROM credentials WHERE integration_id = ?", (p.config_id,))
        _store_credentials(db, integration_id=p.config_id, credentials=merged)
        db.commit()
        return {"ok": True}

    return handle


def make_delete_llm_config_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for delete_llm_config operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Delete an LLM configuration and its credentials."""
        p = validate_params(params, DeleteLLMConfigParams)
        if not _integration_exists(db, integration_id=p.config_id):
            return _not_found_result()
        db.execute("DELETE FROM credentials WHERE integration_id = ?", (p.config_id,))
        db.execute("DELETE FROM integrations WHERE id = ?", (p.config_id,))
        db.commit()
        return {"ok": True}

    return handle


def make_activate_llm_config_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for activate_llm_config operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Activate one LLM configuration and deactivate the rest."""
        p = validate_params(params, ActivateLLMConfigParams)
        if not _integration_exists(db, integration_id=p.config_id):
            return _not_found_result()
        db.execute(DEACTIVATE_LLM_CONFIGS_SQL, LLM_SERVICE_TYPES)
        db.execute("UPDATE integrations SET enabled = 1 WHERE id = ?", (p.config_id,))
        db.commit()
        return {"ok": True}

    return handle


def make_get_llm_config_full_handler(db: DatabaseManager) -> OperationHandler:
    """Create handler for get_llm_config_full operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        """Return a full LLM config including secrets for tests."""
        p = validate_params(params, GetLLMConfigFullParams)
        row = db.execute(
            "SELECT id, service_type, enabled FROM integrations WHERE id = ?",
            (p.config_id,),
        ).fetchone()
        if row is None:
            return _not_found_result()
        integration_id, service_type, enabled = row
        credentials = _credential_values(db, integration_id=integration_id)
        return {
            "id": integration_id,
            "service_type": service_type,
            "enabled": bool(enabled),
            "credentials": credentials,
        }

    return handle
