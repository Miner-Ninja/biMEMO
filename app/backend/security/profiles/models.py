"""Data models for the security-profiles subsystem.

Defines the typed catalog primitives (SettingType, RiskLevel,
SettingConstraints, SettingDef) and the persisted profile
container (SecurityProfile). Pure data containers; storage,
validation, and the resolver live in sibling modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


SettingValue = bool | int | float | str | list[str]


class SettingType(StrEnum):
    """How one setting is rendered and validated.

    TOGGLE: boolean on/off.
    SLIDER_INT / SLIDER_FLOAT: numeric range with optional step and unit.
    ENUM: one value from a closed list of options.
    LIST_STRING: editable list of strings (allowlist, regex patterns).
    TEXT: free-form multi-line text.
    """

    TOGGLE = "toggle"
    SLIDER_INT = "slider_int"
    SLIDER_FLOAT = "slider_float"
    ENUM = "enum"
    LIST_STRING = "list_string"
    TEXT = "text"


class RiskLevel(StrEnum):
    """How dangerous it is to weaken or disable one setting.

    LOW: turning off has limited impact.
    MEDIUM: noticeable surface area increase.
    HIGH: significant relaxation of defenses.
    CRITICAL: never editable; surfaces in the locked section only.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class BuiltinName(StrEnum):
    """Stable identifiers for the three seeded built-in profiles."""

    PARANOID = "paranoid"
    PERSONAL = "personal"
    DEVELOPER = "developer"


@dataclass(frozen=True)
class EnumOption:
    """One option in an enum-typed setting."""

    value: str
    label: str


@dataclass(frozen=True)
class SettingConstraints:
    """Per-type constraints for one setting.

    All fields are optional; a TOGGLE setting carries an empty
    SettingConstraints, a SLIDER_INT carries min/max (and
    optionally step/unit), an ENUM carries options, and a
    LIST_STRING may carry item_regex / placeholder.
    """

    min: int | float | None = None
    max: int | float | None = None
    step: int | float | None = None
    unit: str | None = None
    options: tuple[EnumOption, ...] | None = None
    item_regex: str | None = None
    max_length: int | None = None
    placeholder: str | None = None


@dataclass(frozen=True)
class SettingDef:
    """One catalog entry — the static contract of a single setting.

    Attributes:
        key: Stable dotted identifier consumed by ProfileResolver.
        category: One of the eleven catalog categories.
        label: Short human-readable name shown in the UI.
        description: One- or two-sentence plain-language explanation.
        type: Render and validation kind.
        risk_level: How risky it is to weaken this setting.
        constraints: Per-type constraints (range, options, regex).
        default_paranoid: Value shipped with the paranoid built-in.
        default_personal: Value shipped with the personal built-in.
        default_developer: Value shipped with the developer built-in.
        rationale: Optional longer prose for tooltips ("why does this exist").
        example: Optional concrete mini-scenario shown under the toggle.
        keywords: Search synonyms users might type.
        locked: True for always-on critical defenses; never editable.
    """

    key: str
    category: str
    label: str
    description: str
    type: SettingType
    risk_level: RiskLevel
    default_paranoid: SettingValue
    default_personal: SettingValue
    default_developer: SettingValue
    constraints: SettingConstraints = field(default_factory=SettingConstraints)
    rationale: str | None = None
    example: str | None = None
    keywords: tuple[str, ...] = ()
    locked: bool = False


@dataclass(frozen=True)
class SecurityProfile:
    """A persisted security profile.

    Built-in profiles have is_builtin=True and are read-only at the
    storage layer. Custom profiles are user-created and freely
    edited. The settings dict maps SettingDef.key to its current
    value for this profile.
    """

    id: str
    name: str
    description: str
    is_builtin: bool
    settings: dict[str, SettingValue]
    created_at: str
    updated_at: str


class BuiltinProtectedError(Exception):
    """Raised when a mutating operation targets a built-in profile."""


class LockedSettingError(Exception):
    """Raised when a payload tries to mutate a locked setting key."""

    def __init__(self, key: str) -> None:
        super().__init__(f"Setting is locked and cannot be modified: {key}")
        self.key = key


class ProfileNotFoundError(Exception):
    """Raised when a profile id does not exist in storage."""

    def __init__(self, profile_id: str) -> None:
        super().__init__(f"Profile not found: {profile_id}")
        self.profile_id = profile_id


__all__ = [
    "BuiltinName",
    "BuiltinProtectedError",
    "EnumOption",
    "LockedSettingError",
    "ProfileNotFoundError",
    "RiskLevel",
    "SecurityProfile",
    "SettingConstraints",
    "SettingDef",
    "SettingType",
    "SettingValue",
]
