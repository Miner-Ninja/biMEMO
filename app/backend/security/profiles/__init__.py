"""Security profiles subsystem.

Provides the typed catalog of editable security settings, the
three built-in profiles (paranoid / personal / developer),
storage with idempotent seeding, and a ProfileResolver that
serves the active profile's values to defense modules.
"""

from backend.security.profiles.models import (
    BuiltinName,
    BuiltinProtectedError,
    EnumOption,
    LockedSettingError,
    ProfileNotFoundError,
    RiskLevel,
    SecurityProfile,
    SettingConstraints,
    SettingDef,
    SettingType,
    SettingValue,
)


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
