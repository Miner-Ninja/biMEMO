"""Tests for the security-profile catalog and built-in presets.

Covers the structural invariants the rest of the subsystem relies
on: every entry has copy in both label and description, every
editable key has a value in each built-in preset, and every
locked entry is included in the canonical always-on key set the
validation layer enforces.
"""

from backend.security.profiles.builtins import (
    DEVELOPER_SETTINGS,
    PARANOID_SETTINGS,
    PERSONAL_SETTINGS,
    all_builtin_names,
    get_builtin_description,
    get_builtin_label,
    get_builtin_settings,
)
from backend.security.profiles.catalog import (
    CATEGORY_ORDER,
    get_catalog,
    get_editable_keys,
    get_locked_keys,
    get_setting_def,
    get_settings_by_category,
)


# Always-on locked keys that the validator and resolver must
# refuse to touch. Mirrors the list in spec.md and validation.py.
_ALWAYS_ON_KEYS: frozenset[str] = frozenset(
    {
        "filesystem.paths.deny_read_private_keys",
        "filesystem.paths.deny_path_traversal",
        "network.block_cloud_metadata",
        "network.block_numeric_ip_formats",
        "network.dns_resolve_first",
        "memory.respect_tainted_flag",
        "auth.require_jwt",
        "auth.require_csrf",
        "audit.mask_secrets_in_logs",
    },
)


class TestCatalog:
    """Cover the catalog's structural invariants."""

    def test_every_entry_has_label_and_description(self) -> None:
        """Every catalog entry carries non-empty label and description text."""
        for entry in get_catalog():
            assert entry.label, f"Empty label for {entry.key}"
            assert entry.description, f"Empty description for {entry.key}"

    def test_keys_are_unique(self) -> None:
        """The catalog contains no duplicate keys."""
        keys = [entry.key for entry in get_catalog()]
        assert len(keys) == len(set(keys))

    def test_categories_are_known(self) -> None:
        """Every entry uses one of the declared category ids."""
        for entry in get_catalog():
            assert entry.category in CATEGORY_ORDER, (
                f"{entry.key} uses unknown category {entry.category!r}"
            )

    def test_locked_entries_match_always_on_set(self) -> None:
        """Locked catalog entries match the canonical always-on set."""
        assert get_locked_keys() == _ALWAYS_ON_KEYS

    def test_locked_entries_default_true(self) -> None:
        """Every locked critical defense ships ON across all profiles."""
        for entry in get_catalog():
            if not entry.locked:
                continue
            assert entry.default_paranoid is True
            assert entry.default_personal is True
            assert entry.default_developer is True

    def test_get_settings_by_category_round_trip(self) -> None:
        """Concatenating per-category lists reproduces the full catalog."""
        flat = []
        for category in CATEGORY_ORDER:
            flat.extend(get_settings_by_category(category))
        assert flat == list(get_catalog())

    def test_get_setting_def_returns_none_for_unknown_key(self) -> None:
        """Unknown key lookups return None rather than raising."""
        assert get_setting_def("does.not.exist") is None


class TestBuiltinPresets:
    """Cover the built-in profile presets."""

    def test_three_builtins(self) -> None:
        """all_builtin_names returns the three canonical profile ids."""
        assert all_builtin_names() == ("paranoid", "personal", "developer")

    def test_each_preset_covers_every_editable_key(self) -> None:
        """Built-in dicts include every editable catalog key, no extras."""
        editable = get_editable_keys()
        for name in all_builtin_names():
            settings = get_builtin_settings(name)
            assert set(settings.keys()) == editable, (
                f"{name} preset key set diverges from catalog editable keys"
            )

    def test_label_and_description_present_for_each_builtin(self) -> None:
        """Each built-in profile carries human label and description."""
        for name in all_builtin_names():
            assert get_builtin_label(name)
            assert get_builtin_description(name)

    def test_get_builtin_settings_returns_independent_copy(self) -> None:
        """Mutating a returned dict does not affect the module preset."""
        snapshot = get_builtin_settings("personal")
        snapshot["agent_limits.iterations.max"] = 9999
        assert PERSONAL_SETTINGS["agent_limits.iterations.max"] != 9999

    def test_paranoid_personal_developer_presets_diverge(self) -> None:
        """The three presets actually differ on at least one key."""
        keys_with_changes = []
        for key in PERSONAL_SETTINGS:
            values = (
                PARANOID_SETTINGS[key],
                PERSONAL_SETTINGS[key],
                DEVELOPER_SETTINGS[key],
            )
            if len({repr(value) for value in values}) > 1:
                keys_with_changes.append(key)
        assert keys_with_changes, "Built-in presets are identical"
