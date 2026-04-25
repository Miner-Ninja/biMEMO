"""Validation of security-profile settings payloads.

Single source of truth for "is this settings dict acceptable":
checks every key against the catalog (locked rejection, unknown
key rejection) and every value against its `SettingDef` shape
(toggle bool, slider range, enum option set, list[str] homogeneity,
text max length).

Used at two layers:

- The FastAPI router calls this before dispatching to the DB proxy
  so that 400/409 responses come back immediately and locked-key
  attempts never reach storage.
- The DB-proxy handler also calls this as defense-in-depth in case
  a non-FastAPI client ever opens Socket B.
"""

from __future__ import annotations

from typing import Any

from backend.security.profiles.catalog import (
    get_editable_keys,
    get_locked_keys,
    get_setting_def,
)
from backend.security.profiles.models import (
    LockedSettingError,
    SettingType,
)


class UnknownSettingError(ValueError):
    """Raised when a payload key is not in the catalog."""

    def __init__(self, key: str) -> None:
        super().__init__(f"Unknown setting key: {key}")
        self.key = key


class InvalidSettingValueError(ValueError):
    """Raised when a value violates its SettingDef constraints."""

    def __init__(self, key: str, message: str) -> None:
        super().__init__(f"{key}: {message}")
        self.key = key


def validate_settings_payload(settings: dict[str, Any]) -> None:
    """Validate every key/value in a partial settings payload.

    Args:
        settings: Partial dict mapping ``SettingDef.key`` to a value.

    Raises:
        LockedSettingError: If any key is locked.
        UnknownSettingError: If any key is not in the catalog.
        InvalidSettingValueError: If any value fails its constraint.
    """
    locked = get_locked_keys()
    editable = get_editable_keys()
    for key, value in settings.items():
        if key in locked:
            raise LockedSettingError(key)
        if key not in editable:
            raise UnknownSettingError(key)
        validate_value(key, value)


def validate_value(key: str, value: Any) -> None:
    """Validate one value against the catalog entry for ``key``.

    Args:
        key: Setting key.
        value: Candidate value.

    Raises:
        UnknownSettingError: If the key is not in the catalog.
        InvalidSettingValueError: If the value violates a constraint.
    """
    setting = get_setting_def(key)
    if setting is None:
        raise UnknownSettingError(key)

    constraints = setting.constraints
    setting_type = setting.type

    if setting_type is SettingType.TOGGLE:
        if not isinstance(value, bool):
            raise InvalidSettingValueError(
                key, f"expected bool, got {type(value).__name__}"
            )
        return

    if setting_type in (SettingType.SLIDER_INT, SettingType.SLIDER_FLOAT):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidSettingValueError(
                key, f"expected number, got {type(value).__name__}"
            )
        if setting_type is SettingType.SLIDER_INT and not isinstance(value, int):
            raise InvalidSettingValueError(
                key, f"expected int, got {type(value).__name__}"
            )
        if constraints.min is not None and value < constraints.min:
            raise InvalidSettingValueError(
                key, f"value {value} below min {constraints.min}"
            )
        if constraints.max is not None and value > constraints.max:
            raise InvalidSettingValueError(
                key, f"value {value} above max {constraints.max}"
            )
        return

    if setting_type is SettingType.ENUM:
        if constraints.options is None:
            return
        allowed = {opt.value for opt in constraints.options}
        if value not in allowed:
            raise InvalidSettingValueError(
                key,
                f"value {value!r} not in allowed options {sorted(allowed)}",
            )
        return

    if setting_type is SettingType.LIST_STRING:
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise InvalidSettingValueError(key, "expected list[str]")
        return

    if setting_type is SettingType.TEXT:
        if not isinstance(value, str):
            raise InvalidSettingValueError(
                key, f"expected string, got {type(value).__name__}"
            )
        if (
            constraints.max_length is not None
            and len(value) > constraints.max_length
        ):
            raise InvalidSettingValueError(
                key,
                f"text length {len(value)} above max {constraints.max_length}",
            )
        return


__all__ = [
    "InvalidSettingValueError",
    "UnknownSettingError",
    "validate_settings_payload",
    "validate_value",
]
