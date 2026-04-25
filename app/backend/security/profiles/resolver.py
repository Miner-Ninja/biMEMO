"""Active-profile resolver consumed by every defense module.

ProfileResolver caches the active security profile's settings dict
in memory and answers `get(key)` queries from defense modules.
The cache is loaded once at startup (`load()`) and refreshed on
profile activation (`reload()`). For locked catalog keys the
resolver always returns the locked-default value regardless of
the active profile, so always-on defenses (master-key, cloud
metadata block, etc.) cannot be bent by editing a profile.

A module-level `get_resolver()` is provided for non-DI call sites
(content_filter, validation, rate_limits — modules that run far
from any FastAPI Request and historically used hard-coded
constants).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from backend.security.profiles.catalog import get_setting_def


if TYPE_CHECKING:
    from backend.security.profiles.models import SettingValue
    from backend.security.profiles.storage import ProfileStorage


logger = logging.getLogger(__name__)


class ProfileResolver:
    """In-memory cache of the active profile's settings.

    The cache is loaded once at startup and replaced atomically on
    activation. Reads (`get(key)`) are lock-free; writes (`reload()`)
    serialise through an asyncio.Lock to prevent two concurrent
    activations from racing.

    Attributes:
        active_profile_id: Id of the profile currently serving values
            (None until first load).
    """

    def __init__(self, storage: ProfileStorage) -> None:
        """Initialise the resolver against an admin-side storage wrapper."""
        self._storage = storage
        self._active_id: str | None = None
        self._settings: dict[str, SettingValue] = {}
        self._loaded: bool = False
        self._lock = asyncio.Lock()

    @property
    def active_profile_id(self) -> str | None:
        """Return the currently active profile id, or None if not loaded."""
        return self._active_id

    @property
    def is_loaded(self) -> bool:
        """Return whether the cache has been populated at least once."""
        return self._loaded

    async def load(self) -> None:
        """Populate the cache by fetching the active profile from storage.

        Safe to call multiple times — second and later calls behave like
        `reload()`. Holds the internal lock to serialise with `reload()`.
        """
        async with self._lock:
            await self._load_locked()

    async def reload(self) -> None:
        """Refresh the cache after a profile change.

        Called from the activation endpoint after `set_active_profile_id`
        succeeds. Holds the internal lock for the duration of the
        round-trip so concurrent activations resolve in arrival order.
        """
        async with self._lock:
            await self._load_locked()

    async def _load_locked(self) -> None:
        """Fetch the active profile and atomically swap the cache."""
        active_id, profile = await self._storage.get_active()
        # Atomic swap — Python dict assignment is atomic under the GIL,
        # so concurrent `get()` readers always see a coherent view.
        self._active_id = active_id
        self._settings = dict(profile.settings)
        self._loaded = True
        logger.info(
            "ProfileResolver loaded active profile %r (%d settings)",
            active_id,
            len(self._settings),
        )

    def get(self, key: str, default: Any = None) -> Any:  # noqa: ANN401 -- catalog values are intentionally mixed-type
        """Return the active value for one setting key.

        Resolution order:
        1. Locked catalog entries always return their locked default
           (catalog's `default_personal`). Profiles cannot override
           locked keys.
        2. Editable keys present in the active profile return the
           profile's stored value.
        3. Editable keys missing from the active profile (e.g., the
           profile was created before the catalog grew this key) fall
           back to the catalog's `default_personal`.
        4. Unknown keys return ``default`` (caller-supplied) when given,
           otherwise raise ``KeyError`` so typos surface loudly.

        Args:
            key: SettingDef.key as declared in the catalog.
            default: Fallback for unknown keys; ignored otherwise.

        Returns:
            The resolved setting value.

        Raises:
            KeyError: If the key is not in the catalog and no default
                was provided.
        """
        setting_def = get_setting_def(key)
        if setting_def is None:
            if default is not None:
                return default
            raise KeyError(f"Unknown setting key: {key!r}")
        if setting_def.locked:
            return setting_def.default_personal
        if key in self._settings:
            return self._settings[key]
        return setting_def.default_personal

    def get_bool(self, key: str) -> bool:
        """Return a boolean setting; raises if value is not bool."""
        value = self.get(key)
        if not isinstance(value, bool):
            raise TypeError(f"Setting {key!r} is not bool: {type(value).__name__}")
        return value

    def get_int(self, key: str) -> int:
        """Return an integer setting; raises if value is not int."""
        value = self.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"Setting {key!r} is not int: {type(value).__name__}")
        return value

    def get_float(self, key: str) -> float:
        """Return a float setting; raises if value is not numeric."""
        value = self.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"Setting {key!r} is not numeric: {type(value).__name__}")
        return float(value)

    def get_str(self, key: str) -> str:
        """Return a string setting; raises if value is not str."""
        value = self.get(key)
        if not isinstance(value, str):
            raise TypeError(f"Setting {key!r} is not str: {type(value).__name__}")
        return value

    def get_list_str(self, key: str) -> list[str]:
        """Return a list[str] setting; raises on shape mismatch."""
        value = self.get(key)
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise TypeError(f"Setting {key!r} is not list[str]")
        return list(value)


# ---------------------------------------------------------------------------
# Module-level singleton for non-DI call sites.
# ---------------------------------------------------------------------------
_resolver_instance: ProfileResolver | None = None


def set_resolver(resolver: ProfileResolver | None) -> None:
    """Register (or clear) the process-wide resolver instance.

    Called once from FastAPI lifespan after the admin DB client is
    connected and `resolver.load()` has populated the cache. Tests
    pass a stub instance and clear it on teardown.
    """
    global _resolver_instance
    _resolver_instance = resolver


def get_resolver() -> ProfileResolver:
    """Return the process-wide resolver, raising if not yet registered.

    Defense modules call this from far-away code paths (content_filter,
    validation, rate_limits) where threading a resolver argument
    through every helper would bloat the API surface.

    Raises:
        RuntimeError: If `set_resolver()` was never called. Production
            wiring guarantees this can only happen if a defense module
            runs before the FastAPI lifespan completes — in which case
            the call site is the bug, not the resolver.
    """
    if _resolver_instance is None:
        raise RuntimeError(
            "ProfileResolver has not been initialised. "
            "set_resolver() must be called from FastAPI lifespan before "
            "any defense module fetches a setting."
        )
    return _resolver_instance


__all__ = [
    "ProfileResolver",
    "get_resolver",
    "set_resolver",
]
