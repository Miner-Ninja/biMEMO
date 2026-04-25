"""Tests for the FastAPI /security/* router.

Covers the public HTTP contract: catalog response shape, profile
CRUD round-trip, locked-key 400 translation, built-in 409
rejection, missing-profile 404, and auth gate.

The router is mounted on a minimal FastAPI app and the three
dependencies (auth claims, ProfileStorage, ProfileResolver) are
overridden with in-process stubs. Real HTTP semantics surface
through Starlette's TestClient.
"""

import uuid
from typing import Any

import pytest
from backend.channels.jwt_auth import TokenClaims
from backend.processes.fastapi_app.deps import (
    _get_profile_resolver,
    _get_profile_storage,
    _require_auth,
)
from backend.processes.fastapi_app.routes.security import security_router
from backend.security.profiles.builtins import (
    PARANOID_SETTINGS,
    PERSONAL_SETTINGS,
    get_builtin_description,
)
from backend.security.profiles.models import (
    BuiltinProtectedError,
    ProfileNotFoundError,
    SecurityProfile,
)
from fastapi import FastAPI
from starlette.testclient import TestClient


_FIXED_TIMESTAMP = "2026-01-01T00:00:00.000"


def _build_profile(
    profile_id: str,
    *,
    is_builtin: bool,
    settings: dict[str, Any] | None = None,
) -> SecurityProfile:
    """Construct a SecurityProfile with stable timestamps for assertions."""
    return SecurityProfile(
        id=profile_id,
        name=profile_id,
        description=get_builtin_description(profile_id) if is_builtin else "",
        is_builtin=is_builtin,
        settings=dict(settings or PERSONAL_SETTINGS),
        created_at=_FIXED_TIMESTAMP,
        updated_at=_FIXED_TIMESTAMP,
    )


class _StubStorage:
    """In-memory ProfileStorage stub mirroring the real wrapper API."""

    def __init__(self) -> None:
        """Seed the stub with the three built-ins, personal active."""
        self._profiles: dict[str, SecurityProfile] = {
            "paranoid": _build_profile(
                "paranoid",
                is_builtin=True,
                settings=PARANOID_SETTINGS,
            ),
            "personal": _build_profile(
                "personal",
                is_builtin=True,
                settings=PERSONAL_SETTINGS,
            ),
            "developer": _build_profile(
                "developer",
                is_builtin=True,
                settings=PERSONAL_SETTINGS,
            ),
        }
        self._active_id = "personal"

    async def list_profiles(self) -> list[SecurityProfile]:
        """Return all profiles, built-ins first by stable order."""
        return list(self._profiles.values())

    async def get_profile(self, profile_id: str) -> SecurityProfile:
        """Return one profile by id or raise ProfileNotFoundError."""
        if profile_id not in self._profiles:
            raise ProfileNotFoundError(profile_id)
        return self._profiles[profile_id]

    async def get_active(self) -> tuple[str, SecurityProfile]:
        """Return the active profile id and SecurityProfile."""
        return (self._active_id, self._profiles[self._active_id])

    async def set_active(self, profile_id: str) -> str:
        """Activate one profile or raise ProfileNotFoundError."""
        if profile_id not in self._profiles:
            raise ProfileNotFoundError(profile_id)
        self._active_id = profile_id
        return profile_id

    async def create_profile(
        self,
        *,
        name: str,
        description: str = "",
        base_profile_id: str | None = None,
        settings_overrides: dict[str, Any] | None = None,
    ) -> SecurityProfile:
        """Create a fresh custom profile from base + overrides."""
        if base_profile_id and base_profile_id not in self._profiles:
            raise ProfileNotFoundError(base_profile_id)
        base_settings = (
            dict(self._profiles[base_profile_id].settings)
            if base_profile_id
            else dict(PERSONAL_SETTINGS)
        )
        if settings_overrides:
            base_settings.update(settings_overrides)
        new_id = str(uuid.uuid4())
        profile = SecurityProfile(
            id=new_id,
            name=name,
            description=description,
            is_builtin=False,
            settings=base_settings,
            created_at=_FIXED_TIMESTAMP,
            updated_at=_FIXED_TIMESTAMP,
        )
        self._profiles[new_id] = profile
        return profile

    async def update_profile(
        self,
        profile_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        settings: dict[str, Any] | None = None,
    ) -> SecurityProfile:
        """Patch a custom profile or raise BuiltinProtectedError."""
        existing = await self.get_profile(profile_id)
        if existing.is_builtin:
            raise BuiltinProtectedError(profile_id)
        merged_settings = dict(existing.settings)
        if settings:
            merged_settings.update(settings)
        updated = SecurityProfile(
            id=existing.id,
            name=name if name is not None else existing.name,
            description=(
                description if description is not None else existing.description
            ),
            is_builtin=False,
            settings=merged_settings,
            created_at=existing.created_at,
            updated_at=_FIXED_TIMESTAMP,
        )
        self._profiles[profile_id] = updated
        return updated

    async def delete_profile(
        self,
        profile_id: str,
    ) -> tuple[str, str | None]:
        """Delete a custom profile, falling back active to personal."""
        existing = await self.get_profile(profile_id)
        if existing.is_builtin:
            raise BuiltinProtectedError(profile_id)
        new_active: str | None = None
        if self._active_id == profile_id:
            self._active_id = "personal"
            new_active = "personal"
        del self._profiles[profile_id]
        return (profile_id, new_active)


class _StubResolver:
    """ProfileResolver stub that records reload() calls for assertions."""

    def __init__(self, storage: _StubStorage) -> None:
        """Store the storage stub and zero the reload counter."""
        self._storage = storage
        self.reload_calls: int = 0

    @property
    def active_profile_id(self) -> str:
        """Return the underlying storage's active id."""
        return self._storage._active_id

    async def reload(self) -> None:
        """Record one reload call."""
        self.reload_calls += 1


def _build_test_app(
    *,
    storage: _StubStorage,
    resolver: _StubResolver,
    require_auth: bool = True,
) -> FastAPI:
    """Build a FastAPI app with /security/* mounted and deps overridden."""
    app = FastAPI()
    app.include_router(security_router)
    if require_auth:
        app.dependency_overrides[_require_auth] = lambda: TokenClaims(
            jti="test-jti",
            iat=0.0,
            exp=0.0,
            generation=0,
            token_type="access",
        )
    app.dependency_overrides[_get_profile_storage] = lambda: storage
    app.dependency_overrides[_get_profile_resolver] = lambda: resolver
    return app


@pytest.fixture
def storage() -> _StubStorage:
    """Return a fresh stub storage seeded with three built-ins."""
    return _StubStorage()


@pytest.fixture
def resolver(storage: _StubStorage) -> _StubResolver:
    """Return a stub resolver bound to the storage."""
    return _StubResolver(storage)


@pytest.fixture
def client(
    storage: _StubStorage,
    resolver: _StubResolver,
) -> TestClient:
    """Return a TestClient against the auth-overridden app."""
    return TestClient(
        _build_test_app(storage=storage, resolver=resolver, require_auth=True),
    )


class TestSecurityCatalog:
    """Cover GET /security/catalog."""

    def test_catalog_response_shape(self, client: TestClient) -> None:
        """Catalog returns categories list and settings array."""
        response = client.get("/security/catalog")
        assert response.status_code == 200
        body = response.json()
        assert "categories" in body
        assert "settings" in body
        assert len(body["settings"]) > 0
        first = body["settings"][0]
        for field in (
            "key",
            "category",
            "label",
            "description",
            "type",
            "risk_level",
            "constraints",
            "locked",
        ):
            assert field in first

    def test_catalog_includes_locked_entries(self, client: TestClient) -> None:
        """Locked critical defenses appear in the catalog response."""
        response = client.get("/security/catalog")
        assert response.status_code == 200
        body = response.json()
        locked_keys = {entry["key"] for entry in body["settings"] if entry["locked"]}
        assert "network.block_cloud_metadata" in locked_keys
        assert "auth.require_jwt" in locked_keys


class TestSecurityProfilesList:
    """Cover GET /security/profiles and /security/profiles/{id}."""

    def test_list_returns_three_builtins(self, client: TestClient) -> None:
        """All three seeded built-ins appear in the list response."""
        response = client.get("/security/profiles")
        assert response.status_code == 200
        ids = {profile["id"] for profile in response.json()["profiles"]}
        assert ids == {"paranoid", "personal", "developer"}

    def test_get_active_returns_personal(self, client: TestClient) -> None:
        """Active profile lookup returns personal by default."""
        response = client.get("/security/profiles/active")
        assert response.status_code == 200
        body = response.json()
        assert body["active_profile_id"] == "personal"
        assert body["profile"]["id"] == "personal"

    def test_get_one_by_id(self, client: TestClient) -> None:
        """Single profile lookup by id returns matching record."""
        response = client.get("/security/profiles/developer")
        assert response.status_code == 200
        assert response.json()["profile"]["id"] == "developer"

    def test_get_unknown_returns_404(self, client: TestClient) -> None:
        """Unknown profile id surfaces as HTTP 404."""
        response = client.get("/security/profiles/ghost")
        assert response.status_code == 404


class TestSecurityProfilesCreate:
    """Cover POST /security/profiles."""

    def test_create_from_developer_clones_settings(
        self,
        client: TestClient,
    ) -> None:
        """Cloning copies the source profile's settings."""
        response = client.post(
            "/security/profiles",
            json={
                "name": "my-dev",
                "base_profile_id": "developer",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["profile"]["name"] == "my-dev"
        assert body["profile"]["is_builtin"] is False

    def test_create_with_locked_key_returns_400(
        self,
        client: TestClient,
    ) -> None:
        """Settings overrides cannot include locked keys (HTTP 400)."""
        response = client.post(
            "/security/profiles",
            json={
                "name": "bad",
                "settings": {"network.block_cloud_metadata": False},
            },
        )
        assert response.status_code == 400
        body = response.json()
        assert body["detail"]["error"] == "setting is locked"
        assert body["detail"]["key"] == "network.block_cloud_metadata"

    def test_create_with_unknown_setting_returns_400(
        self,
        client: TestClient,
    ) -> None:
        """Unknown catalog keys are rejected with HTTP 400."""
        response = client.post(
            "/security/profiles",
            json={
                "name": "bad",
                "settings": {"does.not.exist": 1},
            },
        )
        assert response.status_code == 400
        assert response.json()["detail"]["error"] == "unknown setting"


class TestSecurityProfilesUpdate:
    """Cover PATCH /security/profiles/{id}."""

    def _create_custom(self, client: TestClient) -> str:
        """Helper: create a custom profile and return its id."""
        response = client.post(
            "/security/profiles",
            json={"name": "to-edit", "base_profile_id": "personal"},
        )
        assert response.status_code == 201
        return response.json()["profile"]["id"]

    def test_update_builtin_returns_409(self, client: TestClient) -> None:
        """Patching a built-in returns HTTP 409."""
        response = client.patch(
            "/security/profiles/personal",
            json={"description": "hacked"},
        )
        assert response.status_code == 409

    def test_update_custom_succeeds(self, client: TestClient) -> None:
        """A custom profile accepts name/description/settings updates."""
        custom_id = self._create_custom(client)
        response = client.patch(
            f"/security/profiles/{custom_id}",
            json={
                "description": "updated",
                "settings": {"agent_limits.iterations.max": 75},
            },
        )
        assert response.status_code == 200
        profile = response.json()["profile"]
        assert profile["description"] == "updated"
        assert profile["settings"]["agent_limits.iterations.max"] == 75

    def test_update_with_locked_key_returns_400(
        self,
        client: TestClient,
    ) -> None:
        """Locked-key payload still rejected on PATCH."""
        custom_id = self._create_custom(client)
        response = client.patch(
            f"/security/profiles/{custom_id}",
            json={"settings": {"auth.require_jwt": False}},
        )
        assert response.status_code == 400


class TestSecurityProfilesDelete:
    """Cover DELETE /security/profiles/{id} and active-fallback."""

    def test_delete_builtin_returns_409(self, client: TestClient) -> None:
        """Deleting a built-in returns HTTP 409."""
        response = client.delete("/security/profiles/personal")
        assert response.status_code == 409

    def test_delete_active_custom_falls_back(
        self,
        client: TestClient,
        resolver: _StubResolver,
    ) -> None:
        """Deleting the active custom profile sets active to personal."""
        create = client.post(
            "/security/profiles",
            json={"name": "victim"},
        )
        custom_id = create.json()["profile"]["id"]

        client.post(f"/security/profiles/{custom_id}/activate")
        response = client.delete(f"/security/profiles/{custom_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["deleted_id"] == custom_id
        assert body["new_active_id"] == "personal"
        # Resolver reload was triggered by the fallback.
        assert resolver.reload_calls == 2  # once on activate, once on delete


class TestSecurityProfilesActivate:
    """Cover POST /security/profiles/{id}/activate."""

    def test_activate_known_profile(
        self,
        client: TestClient,
        resolver: _StubResolver,
    ) -> None:
        """Activation returns the new active id and reloads the resolver."""
        response = client.post("/security/profiles/developer/activate")
        assert response.status_code == 200
        assert response.json()["active_profile_id"] == "developer"
        assert resolver.reload_calls == 1

    def test_activate_unknown_returns_404(
        self,
        client: TestClient,
    ) -> None:
        """Activating an unknown id returns HTTP 404."""
        response = client.post("/security/profiles/ghost/activate")
        assert response.status_code == 404


class TestSecurityAuthGate:
    """Cover the auth gate guarding every /security/* endpoint."""

    def test_unauthenticated_request_returns_401(
        self,
        storage: _StubStorage,
        resolver: _StubResolver,
    ) -> None:
        """When _require_auth is not overridden, every endpoint is 401."""
        app = _build_test_app(
            storage=storage,
            resolver=resolver,
            require_auth=False,
        )
        client = TestClient(app)
        response = client.get("/security/catalog")
        assert response.status_code == 401
