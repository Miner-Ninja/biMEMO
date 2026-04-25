"""HTTP routes for security profile management (`/security/*`).

Wraps the FastAPI side of the security-profile subsystem:
serves the catalog, lists/CRUDs profiles, exposes activation,
and reloads `ProfileResolver` after every active-profile change.
All endpoints require authentication (and CSRF on mutating verbs)
via the standard middleware; this router only translates business
errors to HTTP statuses.

Mapping:
- BuiltinProtectedError → 409 Conflict
- LockedSettingError    → 400 Bad Request
- ProfileNotFoundError  → 404 Not Found
- Any validation error  → 400 Bad Request
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException

from backend.channels.jwt_auth import (
    TokenClaims,  # noqa: TC001 -- used by FastAPI Depends() at runtime
)
from backend.models.security import (
    ActivateProfileResponse,
    ActiveProfileResponse,
    CatalogResponse,
    ConstraintsPayload,
    CreateProfileRequest,
    DeleteProfileResponse,
    EnumOptionPayload,
    ProfilePayload,
    ProfileResponse,
    ProfilesListResponse,
    SettingPayload,
    UpdateProfileRequest,
)
from backend.security.profiles.catalog import (
    CATEGORY_ORDER,
    get_catalog,
)
from backend.security.profiles.models import (
    BuiltinProtectedError,
    LockedSettingError,
    ProfileNotFoundError,
    SecurityProfile,
    SettingDef,
)
from backend.security.profiles.validation import (
    InvalidSettingValueError,
    UnknownSettingError,
    validate_settings_payload,
)

from ..deps import (
    _get_profile_resolver,
    _get_profile_storage,
    _require_auth,
)


if TYPE_CHECKING:
    from backend.security.profiles.resolver import ProfileResolver
    from backend.security.profiles.storage import ProfileStorage


security_router = APIRouter(prefix="/security")


# ---------------------------------------------------------------------------
# Conversion helpers (dataclass → Pydantic payload)
# ---------------------------------------------------------------------------


def _setting_to_payload(setting: SettingDef) -> SettingPayload:
    """Project one catalog SettingDef into the wire shape."""
    constraints = setting.constraints
    options = (
        [
            EnumOptionPayload(value=opt.value, label=opt.label)
            for opt in constraints.options
        ]
        if constraints.options is not None
        else None
    )
    return SettingPayload(
        key=setting.key,
        category=setting.category,
        label=setting.label,
        description=setting.description,
        type=setting.type.value,
        risk_level=setting.risk_level.value,
        constraints=ConstraintsPayload(
            min=constraints.min,
            max=constraints.max,
            step=constraints.step,
            unit=constraints.unit,
            options=options,
            item_regex=constraints.item_regex,
            max_length=constraints.max_length,
            placeholder=constraints.placeholder,
        ),
        default_paranoid=setting.default_paranoid,
        default_personal=setting.default_personal,
        default_developer=setting.default_developer,
        rationale=setting.rationale,
        example=setting.example,
        keywords=list(setting.keywords),
        locked=setting.locked,
    )


def _profile_to_payload(profile: SecurityProfile) -> ProfilePayload:
    """Project one SecurityProfile dataclass into the wire shape."""
    return ProfilePayload(**dataclasses.asdict(profile))


# ---------------------------------------------------------------------------
# Validation pre-checks (translate business errors to HTTP status codes)
# ---------------------------------------------------------------------------


def _validate_or_400(settings: dict[str, Any] | None) -> None:
    """Run shared validation; surface failures as HTTP 400/409."""
    if settings is None:
        return
    try:
        validate_settings_payload(settings)
    except LockedSettingError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "setting is locked", "key": exc.key},
        ) from exc
    except UnknownSettingError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "unknown setting", "key": exc.key},
        ) from exc
    except InvalidSettingValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid setting value",
                "key": exc.key,
                "message": str(exc),
            },
        ) from exc


async def _require_custom_or_409(
    storage: ProfileStorage,
    profile_id: str,
) -> SecurityProfile:
    """Fetch a profile and reject built-ins with 409, missing with 404."""
    try:
        profile = await storage.get_profile(profile_id)
    except ProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="profile not found") from exc
    except HTTPException:
        # Underlying client may already have raised a translated status.
        raise
    if profile.is_builtin:
        raise HTTPException(
            status_code=409,
            detail={"error": "built-in profile is read-only", "id": profile_id},
        )
    return profile


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@security_router.get("/catalog", response_model=CatalogResponse)
async def get_catalog_endpoint(
    _claims: TokenClaims = Depends(_require_auth),  # noqa: B008
) -> CatalogResponse:
    """Return the full catalog of editable and locked settings."""
    return CatalogResponse(
        categories=list(CATEGORY_ORDER),
        settings=[_setting_to_payload(entry) for entry in get_catalog()],
    )


@security_router.get("/profiles", response_model=ProfilesListResponse)
async def list_profiles_endpoint(
    _claims: TokenClaims = Depends(_require_auth),  # noqa: B008
    storage: ProfileStorage = Depends(_get_profile_storage),  # noqa: B008
) -> ProfilesListResponse:
    """List all profiles, built-ins first."""
    profiles = await storage.list_profiles()
    return ProfilesListResponse(
        profiles=[_profile_to_payload(p) for p in profiles],
    )


@security_router.get("/profiles/active", response_model=ActiveProfileResponse)
async def get_active_profile_endpoint(
    _claims: TokenClaims = Depends(_require_auth),  # noqa: B008
    storage: ProfileStorage = Depends(_get_profile_storage),  # noqa: B008
) -> ActiveProfileResponse:
    """Return the currently active profile and its id."""
    active_id, profile = await storage.get_active()
    return ActiveProfileResponse(
        active_profile_id=active_id,
        profile=_profile_to_payload(profile),
    )


@security_router.get("/profiles/{profile_id}", response_model=ProfileResponse)
async def get_profile_endpoint(
    profile_id: str,
    _claims: TokenClaims = Depends(_require_auth),  # noqa: B008
    storage: ProfileStorage = Depends(_get_profile_storage),  # noqa: B008
) -> ProfileResponse:
    """Return one profile by id."""
    try:
        profile = await storage.get_profile(profile_id)
    except ProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="profile not found") from exc
    return ProfileResponse(profile=_profile_to_payload(profile))


@security_router.post("/profiles", response_model=ProfileResponse, status_code=201)
async def create_profile_endpoint(
    body: CreateProfileRequest,
    _claims: TokenClaims = Depends(_require_auth),  # noqa: B008
    storage: ProfileStorage = Depends(_get_profile_storage),  # noqa: B008
) -> ProfileResponse:
    """Create a custom profile, optionally cloning an existing one."""
    _validate_or_400(body.settings)
    try:
        profile = await storage.create_profile(
            name=body.name,
            description=body.description,
            base_profile_id=body.base_profile_id,
            settings_overrides=body.settings,
        )
    except ProfileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="base profile not found",
        ) from exc
    return ProfileResponse(profile=_profile_to_payload(profile))


@security_router.patch("/profiles/{profile_id}", response_model=ProfileResponse)
async def update_profile_endpoint(
    profile_id: str,
    body: UpdateProfileRequest,
    _claims: TokenClaims = Depends(_require_auth),  # noqa: B008
    storage: ProfileStorage = Depends(_get_profile_storage),  # noqa: B008
) -> ProfileResponse:
    """Patch a custom profile (name, description, settings)."""
    _validate_or_400(body.settings)
    await _require_custom_or_409(storage, profile_id)
    try:
        profile = await storage.update_profile(
            profile_id,
            name=body.name,
            description=body.description,
            settings=body.settings,
        )
    except BuiltinProtectedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ProfileResponse(profile=_profile_to_payload(profile))


@security_router.delete(
    "/profiles/{profile_id}",
    response_model=DeleteProfileResponse,
)
async def delete_profile_endpoint(
    profile_id: str,
    _claims: TokenClaims = Depends(_require_auth),  # noqa: B008
    storage: ProfileStorage = Depends(_get_profile_storage),  # noqa: B008
    resolver: ProfileResolver = Depends(_get_profile_resolver),  # noqa: B008
) -> DeleteProfileResponse:
    """Delete a custom profile; falls back to personal if it was active."""
    await _require_custom_or_409(storage, profile_id)
    try:
        deleted_id, new_active_id = await storage.delete_profile(profile_id)
    except BuiltinProtectedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if new_active_id is not None:
        # The DB layer fell back to personal; refresh the resolver cache.
        await resolver.reload()
    return DeleteProfileResponse(
        deleted_id=deleted_id,
        new_active_id=new_active_id,
    )


@security_router.post(
    "/profiles/{profile_id}/activate",
    response_model=ActivateProfileResponse,
)
async def activate_profile_endpoint(
    profile_id: str,
    _claims: TokenClaims = Depends(_require_auth),  # noqa: B008
    storage: ProfileStorage = Depends(_get_profile_storage),  # noqa: B008
    resolver: ProfileResolver = Depends(_get_profile_resolver),  # noqa: B008
) -> ActivateProfileResponse:
    """Activate one profile and reload the resolver."""
    try:
        active_id = await storage.set_active(profile_id)
    except ProfileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="profile not found") from exc
    await resolver.reload()
    return ActivateProfileResponse(active_profile_id=active_id)
