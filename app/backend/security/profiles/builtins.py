"""Built-in profile presets seeded by the migration.

Each preset is a dict that maps every editable catalog key to its
shipped value for that profile. Locked keys are not stored here —
the resolver serves their always-on values directly from the
catalog. The dicts are derived from the catalog at import time so
adding a new SettingDef automatically populates all three presets
without manual sync.
"""

from __future__ import annotations

from backend.security.profiles.catalog import CATALOG
from backend.security.profiles.models import BuiltinName, SettingValue


def _preset_from_attr(default_attr: str) -> dict[str, SettingValue]:
    """Build one preset dict from the named default attribute on SettingDef."""
    return {
        entry.key: getattr(entry, default_attr)
        for entry in CATALOG
        if not entry.locked
    }


PARANOID_SETTINGS: dict[str, SettingValue] = _preset_from_attr("default_paranoid")
PERSONAL_SETTINGS: dict[str, SettingValue] = _preset_from_attr("default_personal")
DEVELOPER_SETTINGS: dict[str, SettingValue] = _preset_from_attr("default_developer")


_BUILTIN_BY_NAME: dict[str, dict[str, SettingValue]] = {
    BuiltinName.PARANOID.value: PARANOID_SETTINGS,
    BuiltinName.PERSONAL.value: PERSONAL_SETTINGS,
    BuiltinName.DEVELOPER.value: DEVELOPER_SETTINGS,
}


_BUILTIN_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    BuiltinName.PARANOID.value: (
        "Параноид",
        "Максимальная защита: все блокировки включены, лимиты урезаны.",
    ),
    BuiltinName.PERSONAL.value: (
        "Обычный",
        "Сбалансированный режим — текущее поведение biMEMO для личного помощника.",
    ),
    BuiltinName.DEVELOPER.value: (
        "Программист",
        "Свобода для кода: ослаблены input/output-фильтры, разрешены локальные сети, расширены лимиты.",
    ),
}


def get_builtin_settings(name: str) -> dict[str, SettingValue]:
    """Return a fresh copy of one built-in preset's settings dict.

    Args:
        name: One of "paranoid", "personal", "developer".

    Returns:
        A new dict (callers may freely mutate).

    Raises:
        KeyError: If name is not a known built-in.
    """
    return dict(_BUILTIN_BY_NAME[name])


def get_builtin_label(name: str) -> str:
    """Return the human label for one built-in profile."""
    return _BUILTIN_DESCRIPTIONS[name][0]


def get_builtin_description(name: str) -> str:
    """Return the short description for one built-in profile."""
    return _BUILTIN_DESCRIPTIONS[name][1]


def all_builtin_names() -> tuple[str, ...]:
    """Return the three built-in profile names in stable order."""
    return (
        BuiltinName.PARANOID.value,
        BuiltinName.PERSONAL.value,
        BuiltinName.DEVELOPER.value,
    )


__all__ = [
    "DEVELOPER_SETTINGS",
    "PARANOID_SETTINGS",
    "PERSONAL_SETTINGS",
    "all_builtin_names",
    "get_builtin_description",
    "get_builtin_label",
    "get_builtin_settings",
]
