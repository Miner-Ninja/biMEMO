"""Tests for compute configuration loading and validation.

Covers valid configs, missing files, invalid JSON, missing fields,
invalid modes, and GPU layer validation.
"""

import json

import pytest
from backend.llm.compute_config import (
    ComputeConfigError,
    ComputeMode,
    load_compute_config,
)


class TestLoadGpuOnly:
    """Tests for GPU-only configuration."""

    def test_gpu_only_mode(self, tmp_path):
        """Verify GPU-only config loads correctly."""
        config_file = tmp_path / "compute.json"
        config_file.write_text(json.dumps({"mode": "gpu_only"}))
        config = load_compute_config(str(config_file))
        assert config.mode == ComputeMode.GPU_ONLY
        assert config.gpu_layers == 0


class TestLoadMixed:
    """Tests for mixed GPU+CPU configuration."""

    def test_mixed_mode_with_layers(self, tmp_path):
        """Verify mixed mode loads gpu_layers."""
        config_file = tmp_path / "compute.json"
        config_file.write_text(json.dumps({"mode": "mixed", "gpu_layers": 20}))
        config = load_compute_config(str(config_file))
        assert config.mode == ComputeMode.MIXED
        assert config.gpu_layers == 20

    def test_mixed_mode_default_layers(self, tmp_path):
        """Verify mixed mode defaults gpu_layers to 0."""
        config_file = tmp_path / "compute.json"
        config_file.write_text(json.dumps({"mode": "mixed"}))
        config = load_compute_config(str(config_file))
        assert config.mode == ComputeMode.MIXED
        assert config.gpu_layers == 0


class TestLoadErrors:
    """Tests for configuration error handling."""

    def test_missing_file(self, tmp_path):
        """Verify error on missing config file."""
        with pytest.raises(ComputeConfigError, match="not found"):
            load_compute_config(str(tmp_path / "nonexistent.json"))

    def test_invalid_json(self, tmp_path):
        """Verify error on malformed JSON."""
        config_file = tmp_path / "compute.json"
        config_file.write_text("{bad json")
        with pytest.raises(ComputeConfigError, match="Failed to read"):
            load_compute_config(str(config_file))

    def test_not_object(self, tmp_path):
        """Verify error when JSON is not an object."""
        config_file = tmp_path / "compute.json"
        config_file.write_text(json.dumps([1, 2, 3]))
        with pytest.raises(ComputeConfigError, match="JSON object"):
            load_compute_config(str(config_file))

    def test_missing_mode(self, tmp_path):
        """Verify error when mode field is missing."""
        config_file = tmp_path / "compute.json"
        config_file.write_text(json.dumps({"gpu_layers": 10}))
        with pytest.raises(ComputeConfigError, match=r"Missing.*mode"):
            load_compute_config(str(config_file))

    def test_invalid_mode(self, tmp_path):
        """Verify error on invalid compute mode."""
        config_file = tmp_path / "compute.json"
        config_file.write_text(json.dumps({"mode": "turbo"}))
        with pytest.raises(ComputeConfigError, match="Invalid compute mode"):
            load_compute_config(str(config_file))

    def test_negative_gpu_layers(self, tmp_path):
        """Verify error on negative gpu_layers."""
        config_file = tmp_path / "compute.json"
        config_file.write_text(json.dumps({"mode": "mixed", "gpu_layers": -1}))
        with pytest.raises(ComputeConfigError, match="non-negative"):
            load_compute_config(str(config_file))

    def test_non_int_gpu_layers(self, tmp_path):
        """Verify error on non-integer gpu_layers."""
        config_file = tmp_path / "compute.json"
        config_file.write_text(json.dumps({"mode": "mixed", "gpu_layers": "ten"}))
        with pytest.raises(ComputeConfigError, match="non-negative"):
            load_compute_config(str(config_file))
