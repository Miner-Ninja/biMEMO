"""Compute configuration for LLM inference mode.

Loads GPU/CPU compute settings from a JSON config file.
Applied only on container restart — runtime changes are ignored.
System refuses to start on missing or invalid configuration.
"""

import json
import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


logger = logging.getLogger(__name__)


class ComputeMode(StrEnum):
    """LLM inference compute mode.

    GPU_ONLY runs all inference on GPU (default).
    MIXED offloads some layers to CPU.
    """

    GPU_ONLY = "gpu_only"
    MIXED = "mixed"


@dataclass(frozen=True)
class ComputeConfig:
    """Compute configuration for LLM inference.

    Attributes:
        mode: GPU-only or mixed GPU+CPU mode.
        gpu_layers: Number of layers to run on GPU (mixed mode).
    """

    mode: ComputeMode
    gpu_layers: int


class ComputeConfigError(Exception):
    """Raised when compute config is missing or invalid."""


def _load_compute_config_json(path: Path) -> object:
    """Read and decode the raw compute-config JSON payload."""
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise ComputeConfigError(f"Failed to read compute config: {e}") from e


def _require_existing_config_path(path: str) -> Path:
    """Return an existing compute-config path or raise a config error."""
    config_path = Path(path)
    if not config_path.exists():
        raise ComputeConfigError(f"Compute config file not found: {path}")
    return config_path


def _parse_compute_mode(raw: dict[str, object]) -> ComputeMode:
    """Return the configured compute mode or raise a config error."""
    mode_str = raw.get("mode")
    if mode_str is None:
        raise ComputeConfigError("Missing required field: 'mode'")
    try:
        return ComputeMode(mode_str)
    except ValueError:
        raise ComputeConfigError(  # noqa: B904
            f"Invalid compute mode: '{mode_str}'. Must be 'gpu_only' or 'mixed'"
        )


def _parse_gpu_layers(raw: dict[str, object]) -> int:
    """Return the GPU layer count for mixed mode."""
    gpu_layers = raw.get("gpu_layers", 0)
    if not isinstance(gpu_layers, int) or gpu_layers < 0:
        raise ComputeConfigError("gpu_layers must be a non-negative integer")
    return gpu_layers


def _resolved_gpu_layers(
    *,
    mode: ComputeMode,
    raw: dict[str, object],
) -> int:
    """Return the GPU layer count implied by the selected compute mode."""
    if mode != ComputeMode.MIXED:
        return 0
    return _parse_gpu_layers(raw)


def load_compute_config(path: str) -> ComputeConfig:
    """Load compute configuration from a JSON file.

    Args:
        path: Path to the compute config JSON file.

    Returns:
        Validated ComputeConfig.

    Raises:
        ComputeConfigError: If file is missing or invalid.
    """
    config_path = _require_existing_config_path(path)
    raw = _load_compute_config_json(config_path)

    if not isinstance(raw, dict):
        raise ComputeConfigError("Compute config must be a JSON object")

    mode = _parse_compute_mode(raw)

    gpu_layers = _resolved_gpu_layers(mode=mode, raw=raw)

    config = ComputeConfig(mode=mode, gpu_layers=gpu_layers)
    logger.info(
        "Loaded compute config: mode=%s, gpu_layers=%d",
        config.mode.value,
        config.gpu_layers,
    )
    return config
