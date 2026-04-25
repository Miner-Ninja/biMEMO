"""Typed FastAPI-side wrapper over the security-profile DB operations.

`ProfileStorage` thinly wraps `AdminDBClient.send_operation(...)` calls
and converts the JSON-friendly dicts returned by the handlers into
frozen `SecurityProfile` dataclass instances. The wrapper does no
business logic of its own; HTTP-status translation and locked-key
validation live one layer up in the FastAPI router.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.security.profiles.models import SecurityProfile


if TYPE_CHECKING:
    from backend.processes.fastapi_app.admin_db_client import AdminDBClient


def _profile_from_payload(payload: dict[str, Any]) -> SecurityProfile:
    """Convert one JSON payload from the IPC layer into a SecurityProfile."""
    return SecurityProfile(
        id=payload["id"],
        name=payload["name"],
        description=payload["description"],
        is_builtin=bool(payload["is_builtin"]),
        settings=dict(payload["settings"]),
        created_at=payload["created_at"],
        updated_at=payload["updated_at"],
    )


class ProfileStorage:
    """Typed CRUD wrapper for security profiles.

    Pass-through to the DB proxy via AdminDBClient. Errors surface
    as the HTTPException raised by the underlying IPC client (see
    `_BaseIPCClient` for the translation layer).
    """

    def __init__(self, client: AdminDBClient) -> None:
        self._client = client

    async def list_profiles(self) -> list[SecurityProfile]:
        """Return all built-in and custom profiles, built-ins first."""
        result = await self._client.send_operation("list_security_profiles")
        payload = result.get("profiles", [])
        return [_profile_from_payload(item) for item in payload]

    async def get_profile(self, profile_id: str) -> SecurityProfile:
        """Return one profile by id."""
        result = await self._client.send_operation(
            "get_security_profile",
            {"id": profile_id},
        )
        return _profile_from_payload(result["profile"])

    async def create_profile(
        self,
        *,
        name: str,
        description: str = "",
        base_profile_id: str | None = None,
        settings_overrides: dict[str, Any] | None = None,
    ) -> SecurityProfile:
        """Create a custom profile, optionally cloning from an existing one."""
        params: dict[str, Any] = {"name": name, "description": description}
        if base_profile_id is not None:
            params["base_profile_id"] = base_profile_id
        if settings_overrides is not None:
            params["settings_overrides"] = settings_overrides
        result = await self._client.send_operation(
            "create_security_profile",
            params,
        )
        return _profile_from_payload(result["profile"])

    async def update_profile(
        self,
        profile_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> SecurityProfile:
        """Patch a custom profile. Built-ins reject this at the DB layer."""
        params: dict[str, Any] = {"id": profile_id}
        if name is not None:
            params["name"] = name
        if description is not None:
            params["description"] = description
        if settings is not None:
            params["settings"] = settings
        result = await self._client.send_operation(
            "update_security_profile",
            params,
        )
        return _profile_from_payload(result["profile"])

    async def delete_profile(self, profile_id: str) -> tuple[str, str | None]:
        """Delete a custom profile.

        Returns the (deleted_id, new_active_id) tuple. ``new_active_id``
        is non-None only when the deleted profile was the active one
        (the DB layer falls back to ``personal``).
        """
        result = await self._client.send_operation(
            "delete_security_profile",
            {"id": profile_id},
        )
        return (result["deleted_id"], result.get("new_active_id"))

    async def get_active(self) -> tuple[str, SecurityProfile]:
        """Return (active_profile_id, profile) of the currently active profile."""
        result = await self._client.send_operation("get_active_security_profile")
        return (
            str(result["active_profile_id"]),
            _profile_from_payload(result["profile"]),
        )

    async def set_active(self, profile_id: str) -> str:
        """Activate a profile and return its id."""
        result = await self._client.send_operation(
            "set_active_security_profile",
            {"id": profile_id},
        )
        return str(result["active_profile_id"])


__all__ = [
    "ProfileStorage",
]
