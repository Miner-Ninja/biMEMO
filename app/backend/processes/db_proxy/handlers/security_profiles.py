"""DB proxy handlers for security profile CRUD and active selection.

Provides the SQL-side counterparts of every operation the FastAPI
profile router exposes: list, get, create, update, delete plus
get_active and set_active. Handlers always return dicts (the IPC
layer carries plain JSON); typed conversion to SecurityProfile
happens on the client side in app/backend/security/profiles/storage.py.

Built-in profiles (is_builtin=1) reject mutating operations by
raising BuiltinProtectedError. Locked catalog keys (catalog
entries with locked=true) reject inclusion in any settings
payload by raising LockedSettingError.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from backend.security.profiles.builtins import PERSONAL_SETTINGS
from backend.security.profiles.models import (
    BuiltinProtectedError,
    ProfileNotFoundError,
)
from backend.security.profiles.validation import validate_settings_payload


if TYPE_CHECKING:
    from backend.security.database import DatabaseManager

from ..common import OperationHandler, validate_params
from ..params import (
    CreateSecurityProfileParams,
    DeleteSecurityProfileParams,
    GetSecurityProfileParams,
    ListSecurityProfilesParams,
    SetActiveSecurityProfileParams,
    UpdateSecurityProfileParams,
)


# ---------------------------------------------------------------------------
# Row converters
# ---------------------------------------------------------------------------
_PROFILE_COLUMNS = (
    "id, name, description, is_builtin, settings_json, created_at, updated_at"
)


def _profile_from_row(row: tuple) -> dict[str, Any]:
    """Convert one security_profiles row tuple into a JSON-friendly dict."""
    return {
        "id": row[0],
        "name": row[1],
        "description": row[2],
        "is_builtin": bool(row[3]),
        "settings": json.loads(row[4]),
        "created_at": row[5],
        "updated_at": row[6],
    }


def _select_profile(db: DatabaseManager, profile_id: str) -> dict[str, Any]:
    """Load one profile by id or raise ProfileNotFoundError."""
    cursor = db.execute(
        f"SELECT {_PROFILE_COLUMNS} FROM security_profiles WHERE id = ?",  # noqa: S608 -- _PROFILE_COLUMNS is a module constant, not user input
        (profile_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ProfileNotFoundError(profile_id)
    return _profile_from_row(row)


def _is_builtin(db: DatabaseManager, profile_id: str) -> bool:
    """Return whether one profile is a built-in (cannot be mutated)."""
    cursor = db.execute(
        "SELECT is_builtin FROM security_profiles WHERE id = ?",
        (profile_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ProfileNotFoundError(profile_id)
    return bool(row[0])


# ---------------------------------------------------------------------------
# Handler factories
# ---------------------------------------------------------------------------
def make_list_security_profiles_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for list_security_profiles operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        validate_params(params, ListSecurityProfilesParams)
        cursor = db.execute(
            f"SELECT {_PROFILE_COLUMNS} FROM security_profiles "  # noqa: S608 -- module constant, not user input
            "ORDER BY is_builtin DESC, created_at ASC"
        )
        profiles = [_profile_from_row(row) for row in cursor.fetchall()]
        return {"profiles": profiles}

    return handle


def make_get_security_profile_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for get_security_profile operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, GetSecurityProfileParams)
        return {"profile": _select_profile(db, p.id)}

    return handle


def make_create_security_profile_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for create_security_profile operation.

    Clones from base_profile_id when provided, otherwise starts
    from the editable defaults of the personal preset (so a
    blank custom profile is still usable). The settings_overrides
    payload, if present, is validated and merged on top.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, CreateSecurityProfileParams)

        if p.base_profile_id:
            base = _select_profile(db, p.base_profile_id)
            settings: dict[str, Any] = dict(base["settings"])
        else:
            settings = dict(PERSONAL_SETTINGS)

        if p.settings_overrides:
            validate_settings_payload(p.settings_overrides)
            settings.update(p.settings_overrides)

        profile_id = str(uuid.uuid4())
        db.execute(
            "INSERT INTO security_profiles "
            "(id, name, description, is_builtin, settings_json) "
            "VALUES (?, ?, ?, 0, ?)",
            (profile_id, p.name, p.description, json.dumps(settings)),
        )
        db.commit()
        return {"profile": _select_profile(db, profile_id)}

    return handle


def make_update_security_profile_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for update_security_profile operation.

    Built-in profiles reject all mutations. Otherwise the patch is
    merged on top of the current settings dict; locked-key
    inclusion is rejected.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, UpdateSecurityProfileParams)

        if _is_builtin(db, p.id):
            raise BuiltinProtectedError(f"Built-in profile {p.id} is read-only")

        existing = _select_profile(db, p.id)
        new_name = p.name if p.name is not None else existing["name"]
        new_description = (
            p.description if p.description is not None else existing["description"]
        )

        if p.settings is not None:
            validate_settings_payload(p.settings)
            merged_settings: dict[str, Any] = dict(existing["settings"])
            merged_settings.update(p.settings)
        else:
            merged_settings = existing["settings"]

        db.execute(
            "UPDATE security_profiles "
            "SET name = ?, description = ?, settings_json = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
            "WHERE id = ?",
            (new_name, new_description, json.dumps(merged_settings), p.id),
        )
        db.commit()
        return {"profile": _select_profile(db, p.id)}

    return handle


def make_delete_security_profile_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for delete_security_profile operation.

    Built-in profiles reject deletion. If the deleted profile is
    currently active, the active selection falls back to
    ``personal``.
    """

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, DeleteSecurityProfileParams)

        if _is_builtin(db, p.id):
            raise BuiltinProtectedError(f"Built-in profile {p.id} cannot be deleted")

        active_row = db.execute(
            "SELECT value FROM app_settings WHERE key = 'active_profile_id'"
        ).fetchone()
        active_id = active_row[0] if active_row is not None else None
        new_active_id: str | None = None

        if active_id == p.id:
            db.execute(
                "UPDATE app_settings SET value = 'personal', "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
                "WHERE key = 'active_profile_id'"
            )
            new_active_id = "personal"

        db.execute("DELETE FROM security_profiles WHERE id = ?", (p.id,))
        db.commit()
        return {"deleted_id": p.id, "new_active_id": new_active_id}

    return handle


def make_get_active_security_profile_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for get_active_security_profile operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        validate_params(params, ListSecurityProfilesParams)
        active_row = db.execute(
            "SELECT value FROM app_settings WHERE key = 'active_profile_id'"
        ).fetchone()
        if active_row is None:
            raise ProfileNotFoundError("active_profile_id")
        active_id = active_row[0]
        return {
            "active_profile_id": active_id,
            "profile": _select_profile(db, active_id),
        }

    return handle


def make_set_active_security_profile_handler(
    db: DatabaseManager,
) -> OperationHandler:
    """Create handler for set_active_security_profile operation."""

    async def handle(params: dict[str, Any] | None) -> dict[str, Any]:
        p = validate_params(params, SetActiveSecurityProfileParams)
        # Verify the target profile exists.
        _select_profile(db, p.id)
        existing = db.execute(
            "SELECT 1 FROM app_settings WHERE key = 'active_profile_id'"
        ).fetchone()
        if existing is None:
            db.execute(
                "INSERT INTO app_settings (key, value) VALUES ('active_profile_id', ?)",
                (p.id,),
            )
        else:
            db.execute(
                "UPDATE app_settings SET value = ?, "
                "updated_at = strftime('%Y-%m-%dT%H:%M:%f', 'now') "
                "WHERE key = 'active_profile_id'",
                (p.id,),
            )
        db.commit()
        return {"active_profile_id": p.id}

    return handle
