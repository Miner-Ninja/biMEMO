"""Compute configuration for Ollama inference mode."""

from dataclasses import dataclass
from enum import StrEnum


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


def default_compute_config() -> ComputeConfig:
    """Return the default Ollama compute configuration."""
    return ComputeConfig(mode=ComputeMode.GPU_ONLY, gpu_layers=0)
