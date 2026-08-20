"""Hardware detection and profile selection for LLM inference.

Detects GPU VRAM, system RAM, and CPU cores at startup.
Maps detected hardware to one of four profiles that determine
which Qwen3 models are used for primary and dialog recorder tasks.
"""

import logging
import os
import subprocess
from dataclasses import dataclass
from enum import StrEnum


logger = logging.getLogger(__name__)


def _unavailable_hardware_value() -> float:
    """Return the fallback numeric value for unavailable hardware data."""
    return 0.0


class HardwareProfile(StrEnum):
    """Available hardware performance profiles.

    Each profile maps to specific Qwen3 model assignments
    and dialog recorder execution modes.
    """

    MINIMAL = "minimal"
    STANDARD = "standard"
    PERFORMANCE = "performance"
    MAXIMAL = "maximal"


class DialogRecorderMode(StrEnum):
    """Execution mode for the Dialog Recorder agent.

    Controls how the Dialog Recorder runs relative to the primary model.
    """

    IDLE = "idle"
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


@dataclass(frozen=True)
class ProfileConfig:
    """Configuration derived from a hardware profile.

    Attributes:
        profile: The selected hardware profile.
        primary_model: Qwen3 model for chat completion.
        dialog_recorder_model: Qwen3 model for Dialog Recorder tasks.
        dialog_recorder_mode: How the Dialog Recorder runs.
        distiller_parallel: Whether Summary Distiller runs in parallel.
    """

    profile: HardwareProfile
    primary_model: str
    dialog_recorder_model: str
    dialog_recorder_mode: DialogRecorderMode
    distiller_parallel: bool


PROFILE_CONFIGS: dict[HardwareProfile, ProfileConfig] = {
    HardwareProfile.MINIMAL: ProfileConfig(
        profile=HardwareProfile.MINIMAL,
        primary_model="qwen3:4b",
        dialog_recorder_model="qwen3:0.6b",
        dialog_recorder_mode=DialogRecorderMode.IDLE,
        distiller_parallel=False,
    ),
    HardwareProfile.STANDARD: ProfileConfig(
        profile=HardwareProfile.STANDARD,
        primary_model="qwen3:8b",
        dialog_recorder_model="qwen3:1.7b",
        dialog_recorder_mode=DialogRecorderMode.SEQUENTIAL,
        distiller_parallel=False,
    ),
    HardwareProfile.PERFORMANCE: ProfileConfig(
        profile=HardwareProfile.PERFORMANCE,
        primary_model="qwen3:14b",
        dialog_recorder_model="qwen3:4b",
        dialog_recorder_mode=DialogRecorderMode.PARALLEL,
        distiller_parallel=False,
    ),
    HardwareProfile.MAXIMAL: ProfileConfig(
        profile=HardwareProfile.MAXIMAL,
        primary_model="qwen3:32b",
        dialog_recorder_model="qwen3:8b",
        dialog_recorder_mode=DialogRecorderMode.PARALLEL,
        distiller_parallel=True,
    ),
}


@dataclass(frozen=True)
class HardwareInfo:
    """Detected hardware capabilities.

    Attributes:
        gpu_vram_gb: Total GPU VRAM in gigabytes.
        ram_gb: Total system RAM in gigabytes.
        cpu_cores: Number of CPU cores.
    """

    gpu_vram_gb: float
    ram_gb: float
    cpu_cores: int


def _run_nvidia_smi() -> subprocess.CompletedProcess[str]:
    """Run nvidia-smi with the fixed query used for VRAM detection."""
    return subprocess.run(
        [  # noqa: S607 — resolved via PATH; absence handled by caller
            "nvidia-smi",
            "--query-gpu=memory.total",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )


def _sum_gpu_memory_mb(output: str) -> float:
    """Sum all GPU memory values returned by nvidia-smi."""
    return sum(
        float(line.strip()) for line in output.strip().split("\n") if line.strip()
    )


def detect_gpu_vram() -> float:
    """Detect total GPU VRAM in gigabytes.

    Uses nvidia-smi to query GPU memory. Returns 0.0 if
    nvidia-smi is not available or fails.

    Returns:
        Total GPU VRAM across all GPUs in GB.
    """
    try:
        result = _run_nvidia_smi()
        if result.returncode != 0:
            logger.warning("nvidia-smi failed: %s", result.stderr.strip())
            return _unavailable_hardware_value()

        total_mb = _sum_gpu_memory_mb(result.stdout)
        return total_mb / 1024.0
    except FileNotFoundError:
        logger.info("nvidia-smi not found, assuming no GPU")
        return _unavailable_hardware_value()
    except subprocess.TimeoutExpired:
        logger.warning("nvidia-smi timed out")
        return _unavailable_hardware_value()


def detect_ram() -> float:
    """Detect total system RAM in gigabytes.

    Reads from /proc/meminfo on Linux.

    Returns:
        Total system RAM in GB.
    """
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return kb / (1024.0 * 1024.0)
    except OSError:
        logger.warning("Failed to read /proc/meminfo")
    return _unavailable_hardware_value()


def detect_cpu_cores() -> int:
    """Detect the number of CPU cores.

    Returns:
        Number of available CPU cores.
    """
    return os.cpu_count() or 1


def detect_hardware() -> HardwareInfo:
    """Detect all hardware capabilities.

    Returns:
        HardwareInfo with GPU VRAM, RAM, and CPU cores.
    """
    return HardwareInfo(
        gpu_vram_gb=detect_gpu_vram(),
        ram_gb=detect_ram(),
        cpu_cores=detect_cpu_cores(),
    )


def _profile_for_hardware(hw: HardwareInfo) -> HardwareProfile:
    """Return the hardware profile that matches the detected resources."""
    if hw.gpu_vram_gb >= 48 and hw.ram_gb >= 64:
        return HardwareProfile.MAXIMAL
    if hw.gpu_vram_gb >= 24 and hw.ram_gb >= 32:
        return HardwareProfile.PERFORMANCE
    if hw.gpu_vram_gb >= 12 and hw.ram_gb >= 16:
        return HardwareProfile.STANDARD
    return HardwareProfile.MINIMAL


def select_profile(hw: HardwareInfo) -> ProfileConfig:
    """Select the hardware profile based on detected capabilities.

    Matches hardware against profile thresholds from highest
    to lowest, returning the best matching profile.

    Args:
        hw: Detected hardware capabilities.

    Returns:
        ProfileConfig for the selected profile.
    """
    profile = _profile_for_hardware(hw)
    config = PROFILE_CONFIGS[profile]
    logger.info(
        "Selected profile '%s': primary=%s, dialog_recorder=%s",
        profile.value,
        config.primary_model,
        config.dialog_recorder_model,
    )
    return config
