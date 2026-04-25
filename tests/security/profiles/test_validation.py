"""Tests for shared settings-payload validation.

Covers the four rejection paths:

- Locked catalog entries cannot appear in any payload.
- Unknown keys are rejected with a typed error.
- Out-of-range / wrong-type values are rejected with a typed error.
- Enum values not in the option list are rejected.

Plus the happy-path payload that mixes every type.
"""

import pytest
from backend.security.profiles.models import LockedSettingError
from backend.security.profiles.validation import (
    InvalidSettingValueError,
    UnknownSettingError,
    validate_settings_payload,
    validate_value,
)


class TestValidateSettingsPayload:
    """Cover the high-level entry point used by the API and handler."""

    def test_locked_key_raises(self) -> None:
        """Locked critical setting keys cannot be edited via payload."""
        with pytest.raises(LockedSettingError) as exc_info:
            validate_settings_payload({"network.block_cloud_metadata": False})
        assert exc_info.value.key == "network.block_cloud_metadata"

    def test_unknown_key_raises(self) -> None:
        """Keys not in the catalog raise UnknownSettingError."""
        with pytest.raises(UnknownSettingError) as exc_info:
            validate_settings_payload({"does.not.exist": 1})
        assert exc_info.value.key == "does.not.exist"

    def test_happy_mixed_types(self) -> None:
        """A payload covering every value type passes validation."""
        validate_settings_payload(
            {
                "input_filter.block_code_execution": False,
                "agent_limits.iterations.max": 50,
                "network.dns_timeout_seconds": 7.5,
                "llm.response_schema": "programmer",
                "filesystem.uploads.allowed_extensions": [".go", ".rs"],
                "llm.system_prompt_addons": "Hello",
            }
        )

    def test_empty_payload_passes(self) -> None:
        """An empty dict is a valid (no-op) settings payload."""
        validate_settings_payload({})


class TestValidateValue:
    """Cover the per-key value validator."""

    def test_toggle_rejects_non_bool(self) -> None:
        """Toggle keys reject integers, strings, and ints-as-bools."""
        with pytest.raises(InvalidSettingValueError):
            validate_value("input_filter.block_code_execution", 1)

    def test_slider_int_rejects_bool(self) -> None:
        """Slider int keys reject Python bools (which are int subclasses)."""
        with pytest.raises(InvalidSettingValueError):
            validate_value("agent_limits.iterations.max", True)

    def test_slider_int_rejects_below_min(self) -> None:
        """Slider int keys reject values below the configured min."""
        with pytest.raises(InvalidSettingValueError) as exc_info:
            validate_value("agent_limits.iterations.max", 0)
        assert "below min" in str(exc_info.value)

    def test_slider_int_rejects_above_max(self) -> None:
        """Slider int keys reject values above the configured max."""
        with pytest.raises(InvalidSettingValueError) as exc_info:
            validate_value("agent_limits.iterations.max", 9999)
        assert "above max" in str(exc_info.value)

    def test_slider_float_accepts_float(self) -> None:
        """Slider float keys accept regular Python floats."""
        validate_value("network.dns_timeout_seconds", 3.5)

    def test_slider_int_rejects_float(self) -> None:
        """Slider int keys reject floats — int and float are distinct types."""
        with pytest.raises(InvalidSettingValueError):
            validate_value("agent_limits.iterations.max", 3.5)

    def test_enum_rejects_unknown_option(self) -> None:
        """Enum keys reject values not present in the options list."""
        with pytest.raises(InvalidSettingValueError) as exc_info:
            validate_value("llm.response_schema", "invalid")
        assert "not in allowed options" in str(exc_info.value)

    def test_enum_accepts_known_option(self) -> None:
        """Enum keys accept any value declared in the catalog options."""
        validate_value("llm.response_schema", "personal")
        validate_value("llm.response_schema", "programmer")
        validate_value("llm.response_schema", "freeform")

    def test_list_string_rejects_non_list(self) -> None:
        """list_string keys reject scalars and dicts."""
        with pytest.raises(InvalidSettingValueError):
            validate_value(
                "filesystem.uploads.allowed_extensions",
                ".py",
            )

    def test_list_string_rejects_mixed_items(self) -> None:
        """list_string keys reject items of mixed type."""
        with pytest.raises(InvalidSettingValueError):
            validate_value(
                "filesystem.uploads.allowed_extensions",
                [".py", 42],
            )

    def test_text_rejects_non_string(self) -> None:
        """Text keys reject non-string payloads."""
        with pytest.raises(InvalidSettingValueError):
            validate_value("llm.system_prompt_addons", 1)

    def test_text_rejects_too_long(self) -> None:
        """Text keys reject content above the catalog's max_length."""
        with pytest.raises(InvalidSettingValueError) as exc_info:
            validate_value("llm.system_prompt_addons", "x" * 5000)
        assert "above max" in str(exc_info.value)

    def test_unknown_key_raises_unknown_setting_error(self) -> None:
        """Unknown keys raise UnknownSettingError, not the generic value error."""
        with pytest.raises(UnknownSettingError):
            validate_value("does.not.exist", 1)
