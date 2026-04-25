"""Pydantic request/response models for the /security/* HTTP routes.

Internal data structures (`SettingDef`, `SecurityProfile`) are
plain dataclasses; this module narrows them down to the wire
contracts the frontend depends on. All request bodies use
`StrictRequestModel` so that unexpected fields are rejected.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.models.requests import StrictRequestModel


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class EnumOptionPayload(BaseModel):
    """One option exposed by an enum-typed setting."""

    model_config = ConfigDict(extra="forbid")

    value: str
    label: str


class ConstraintsPayload(BaseModel):
    """Per-type constraints for one setting (all fields optional)."""

    model_config = ConfigDict(extra="forbid")

    min: int | float | None = None
    max: int | float | None = None
    step: int | float | None = None
    unit: str | None = None
    options: list[EnumOptionPayload] | None = None
    item_regex: str | None = None
    max_length: int | None = None
    placeholder: str | None = None


class SettingPayload(BaseModel):
    """One catalog entry as exposed to the frontend."""

    model_config = ConfigDict(extra="forbid")

    key: str
    category: str
    label: str
    description: str
    type: str
    risk_level: str
    constraints: ConstraintsPayload
    default_paranoid: Any
    default_personal: Any
    default_developer: Any
    rationale: str | None = None
    example: str | None = None
    keywords: list[str] = Field(default_factory=list)
    locked: bool = False


class CatalogResponse(BaseModel):
    """Full catalog response."""

    model_config = ConfigDict(extra="forbid")

    categories: list[str]
    settings: list[SettingPayload]


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


class ProfilePayload(BaseModel):
    """One security profile (built-in or custom) on the wire."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str
    is_builtin: bool
    settings: dict[str, Any]
    created_at: str
    updated_at: str


class ProfilesListResponse(BaseModel):
    """List of all profiles."""

    model_config = ConfigDict(extra="forbid")

    profiles: list[ProfilePayload]


class ProfileResponse(BaseModel):
    """Single profile response."""

    model_config = ConfigDict(extra="forbid")

    profile: ProfilePayload


class ActiveProfileResponse(BaseModel):
    """Active profile lookup response."""

    model_config = ConfigDict(extra="forbid")

    active_profile_id: str
    profile: ProfilePayload


class ActivateProfileResponse(BaseModel):
    """Response payload after activating a profile."""

    model_config = ConfigDict(extra="forbid")

    active_profile_id: str


class DeleteProfileResponse(BaseModel):
    """Response payload after deleting a custom profile."""

    model_config = ConfigDict(extra="forbid")

    deleted_id: str
    new_active_id: str | None = None


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class CreateProfileRequest(StrictRequestModel):
    """POST /security/profiles body."""

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)
    base_profile_id: str | None = Field(default=None, max_length=128)
    settings: dict[str, Any] | None = None


class UpdateProfileRequest(StrictRequestModel):
    """PATCH /security/profiles/{id} body."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    settings: dict[str, Any] | None = None


__all__ = [
    "ActivateProfileResponse",
    "ActiveProfileResponse",
    "CatalogResponse",
    "ConstraintsPayload",
    "CreateProfileRequest",
    "DeleteProfileResponse",
    "EnumOptionPayload",
    "ProfilePayload",
    "ProfileResponse",
    "ProfilesListResponse",
    "SettingPayload",
    "UpdateProfileRequest",
]
