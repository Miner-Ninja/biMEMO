"""Tests for the FastAPI-side typed ProfileStorage wrapper.

The wrapper itself is a thin pass-through over AdminDBClient.
These tests use a stub client that captures every send_operation
call so we can assert the operation name, parameters, and the
typed conversion of the JSON response into SecurityProfile.
"""

from typing import Any

import pytest
from backend.security.profiles.storage import ProfileStorage


class _StubAdminClient:
    """AdminDBClient stub that records calls and returns canned payloads."""

    def __init__(self, responses: dict[str, dict[str, Any]]) -> None:
        """Bind the canned responses dict keyed by operation name."""
        self._responses = responses
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def send_operation(
        self,
        operation: str,
        parameters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record the call and return the canned response."""
        self.calls.append((operation, parameters))
        return self._responses[operation]


def _profile_payload(profile_id: str, *, is_builtin: bool = False) -> dict[str, Any]:
    """Build one minimal profile payload mirroring the IPC shape."""
    return {
        "id": profile_id,
        "name": profile_id,
        "description": "",
        "is_builtin": is_builtin,
        "settings": {"foo": True, "bar": 10},
        "created_at": "2026-01-01T00:00:00.000",
        "updated_at": "2026-01-01T00:00:00.000",
    }


class TestProfileStorage:
    """Cover every typed method on the wrapper."""

    @pytest.mark.asyncio
    async def test_list_profiles_dispatches_and_decodes(self) -> None:
        """list_profiles forwards to list_security_profiles and decodes."""
        client = _StubAdminClient(
            {
                "list_security_profiles": {
                    "profiles": [
                        _profile_payload("paranoid", is_builtin=True),
                        _profile_payload("personal", is_builtin=True),
                    ],
                },
            },
        )
        storage = ProfileStorage(client)  # type: ignore[arg-type]
        result = await storage.list_profiles()

        assert client.calls == [("list_security_profiles", None)]
        assert [profile.id for profile in result] == ["paranoid", "personal"]
        assert all(profile.is_builtin for profile in result)

    @pytest.mark.asyncio
    async def test_get_profile_round_trip(self) -> None:
        """get_profile forwards the id and returns a SecurityProfile."""
        client = _StubAdminClient(
            {
                "get_security_profile": {
                    "profile": _profile_payload("personal", is_builtin=True),
                },
            },
        )
        storage = ProfileStorage(client)  # type: ignore[arg-type]
        profile = await storage.get_profile("personal")

        assert client.calls == [("get_security_profile", {"id": "personal"})]
        assert profile.id == "personal"

    @pytest.mark.asyncio
    async def test_create_profile_passes_optional_fields(self) -> None:
        """create_profile only includes optional fields when provided."""
        client = _StubAdminClient(
            {
                "create_security_profile": {
                    "profile": _profile_payload("custom"),
                },
            },
        )
        storage = ProfileStorage(client)  # type: ignore[arg-type]

        await storage.create_profile(
            name="custom",
            description="x",
            base_profile_id="developer",
            settings_overrides={"foo": False},
        )
        assert client.calls == [
            (
                "create_security_profile",
                {
                    "name": "custom",
                    "description": "x",
                    "base_profile_id": "developer",
                    "settings_overrides": {"foo": False},
                },
            ),
        ]

    @pytest.mark.asyncio
    async def test_create_profile_omits_none_fields(self) -> None:
        """Omitted optional arguments do not appear in the IPC payload."""
        client = _StubAdminClient(
            {
                "create_security_profile": {
                    "profile": _profile_payload("custom"),
                },
            },
        )
        storage = ProfileStorage(client)  # type: ignore[arg-type]

        await storage.create_profile(name="custom")
        assert client.calls == [
            (
                "create_security_profile",
                {"name": "custom", "description": ""},
            ),
        ]

    @pytest.mark.asyncio
    async def test_update_profile_passes_all_fields(self) -> None:
        """update_profile includes name/description/settings when given."""
        client = _StubAdminClient(
            {
                "update_security_profile": {
                    "profile": _profile_payload("custom"),
                },
            },
        )
        storage = ProfileStorage(client)  # type: ignore[arg-type]

        await storage.update_profile(
            "custom",
            name="renamed",
            description="d",
            settings={"foo": False},
        )
        assert client.calls == [
            (
                "update_security_profile",
                {
                    "id": "custom",
                    "name": "renamed",
                    "description": "d",
                    "settings": {"foo": False},
                },
            ),
        ]

    @pytest.mark.asyncio
    async def test_update_profile_omits_none_fields(self) -> None:
        """Omitted optional arguments are not forwarded to the IPC call."""
        client = _StubAdminClient(
            {
                "update_security_profile": {
                    "profile": _profile_payload("custom"),
                },
            },
        )
        storage = ProfileStorage(client)  # type: ignore[arg-type]

        await storage.update_profile("custom")
        assert client.calls == [("update_security_profile", {"id": "custom"})]

    @pytest.mark.asyncio
    async def test_delete_profile_returns_tuple(self) -> None:
        """delete_profile returns (deleted_id, new_active_id) tuple."""
        client = _StubAdminClient(
            {
                "delete_security_profile": {
                    "deleted_id": "custom",
                    "new_active_id": "personal",
                },
            },
        )
        storage = ProfileStorage(client)  # type: ignore[arg-type]

        deleted_id, new_active = await storage.delete_profile("custom")
        assert deleted_id == "custom"
        assert new_active == "personal"

    @pytest.mark.asyncio
    async def test_delete_profile_when_not_active(self) -> None:
        """new_active_id is None when the deleted profile was not active."""
        client = _StubAdminClient(
            {
                "delete_security_profile": {
                    "deleted_id": "custom",
                    "new_active_id": None,
                },
            },
        )
        storage = ProfileStorage(client)  # type: ignore[arg-type]
        _, new_active = await storage.delete_profile("custom")
        assert new_active is None

    @pytest.mark.asyncio
    async def test_get_active_returns_id_and_profile(self) -> None:
        """get_active returns the id alongside the typed profile."""
        client = _StubAdminClient(
            {
                "get_active_security_profile": {
                    "active_profile_id": "personal",
                    "profile": _profile_payload("personal", is_builtin=True),
                },
            },
        )
        storage = ProfileStorage(client)  # type: ignore[arg-type]

        active_id, profile = await storage.get_active()
        assert active_id == "personal"
        assert profile.id == "personal"

    @pytest.mark.asyncio
    async def test_set_active_returns_id_string(self) -> None:
        """set_active returns the active id from the IPC response."""
        client = _StubAdminClient(
            {
                "set_active_security_profile": {
                    "active_profile_id": "developer",
                },
            },
        )
        storage = ProfileStorage(client)  # type: ignore[arg-type]

        active_id = await storage.set_active("developer")
        assert active_id == "developer"
        assert client.calls == [
            ("set_active_security_profile", {"id": "developer"}),
        ]
