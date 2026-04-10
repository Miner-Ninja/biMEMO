"""Tests for hardware detection and profile selection.

Covers GPU VRAM detection, RAM detection, CPU core detection,
hardware profile selection for all four tiers, and edge cases.
"""

import subprocess
from unittest.mock import mock_open, patch

from backend.llm.hardware import (
    DialogRecorderMode,
    HardwareInfo,
    HardwareProfile,
    detect_cpu_cores,
    detect_gpu_vram,
    detect_hardware,
    detect_ram,
    select_profile,
)


class TestDetectGpuVram:
    """Tests for GPU VRAM detection."""

    def test_single_gpu(self):
        """Verify single GPU VRAM detection."""
        result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="8192\n", stderr=""
        )
        with patch(
            "backend.llm.hardware.subprocess.run",
            return_value=result,
        ):
            vram = detect_gpu_vram()
        assert vram == 8.0

    def test_multi_gpu(self):
        """Verify multi-GPU VRAM sums correctly."""
        result = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="24576\n24576\n",
            stderr="",
        )
        with patch(
            "backend.llm.hardware.subprocess.run",
            return_value=result,
        ):
            vram = detect_gpu_vram()
        assert vram == 48.0

    def test_nvidia_smi_not_found(self):
        """Verify returns 0 when nvidia-smi is missing."""
        with patch(
            "backend.llm.hardware.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            vram = detect_gpu_vram()
        assert vram == 0.0

    def test_nvidia_smi_timeout(self):
        """Verify returns 0 when nvidia-smi times out."""
        with patch(
            "backend.llm.hardware.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=10),
        ):
            vram = detect_gpu_vram()
        assert vram == 0.0

    def test_nvidia_smi_failure(self):
        """Verify returns 0 when nvidia-smi exits non-zero."""
        result = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="failed",
        )
        with patch(
            "backend.llm.hardware.subprocess.run",
            return_value=result,
        ):
            vram = detect_gpu_vram()
        assert vram == 0.0


class TestDetectRam:
    """Tests for system RAM detection."""

    def test_reads_meminfo(self):
        """Verify RAM detection from /proc/meminfo."""
        meminfo = "MemTotal:       16384000 kB\nMemFree: 8192 kB\n"
        with patch("builtins.open", mock_open(read_data=meminfo)):
            ram = detect_ram()
        expected = 16384000 / (1024.0 * 1024.0)
        assert abs(ram - expected) < 0.01

    def test_meminfo_not_found(self):
        """Verify returns 0 when /proc/meminfo unavailable."""
        with patch("builtins.open", side_effect=OSError("no file")):
            ram = detect_ram()
        assert ram == 0.0

    def test_meminfo_no_memtotal(self):
        """Verify returns 0 when MemTotal line is missing."""
        meminfo = "MemFree: 8192 kB\nBuffers: 4096 kB\n"
        with patch("builtins.open", mock_open(read_data=meminfo)):
            ram = detect_ram()
        assert ram == 0.0


class TestDetectCpuCores:
    """Tests for CPU core detection."""

    def test_returns_core_count(self):
        """Verify returns os.cpu_count()."""
        with patch(
            "backend.llm.hardware.os.cpu_count",
            return_value=8,
        ):
            cores = detect_cpu_cores()
        assert cores == 8

    def test_none_returns_one(self):
        """Verify returns 1 when os.cpu_count() is None."""
        with patch(
            "backend.llm.hardware.os.cpu_count",
            return_value=None,
        ):
            cores = detect_cpu_cores()
        assert cores == 1


class TestDetectHardware:
    """Tests for full hardware detection."""

    def test_aggregates_all_sources(self):
        """Verify detect_hardware combines all detections."""
        with (
            patch(
                "backend.llm.hardware.detect_gpu_vram",
                return_value=24.0,
            ),
            patch(
                "backend.llm.hardware.detect_ram",
                return_value=32.0,
            ),
            patch(
                "backend.llm.hardware.detect_cpu_cores",
                return_value=16,
            ),
        ):
            hw = detect_hardware()
        assert hw.gpu_vram_gb == 24.0
        assert hw.ram_gb == 32.0
        assert hw.cpu_cores == 16


class TestSelectProfile:
    """Tests for hardware profile selection."""

    def test_minimal_profile(self):
        """Verify minimal hardware selects minimal profile."""
        hw = HardwareInfo(gpu_vram_gb=8.0, ram_gb=8.0, cpu_cores=4)
        config = select_profile(hw)
        assert config.profile == HardwareProfile.MINIMAL
        assert config.primary_model == "qwen3:4b"
        assert config.dialog_recorder_model == "qwen3:0.6b"
        assert config.dialog_recorder_mode == DialogRecorderMode.IDLE
        assert not config.distiller_parallel

    def test_standard_profile(self):
        """Verify standard hardware selects standard profile."""
        hw = HardwareInfo(gpu_vram_gb=12.0, ram_gb=16.0, cpu_cores=8)
        config = select_profile(hw)
        assert config.profile == HardwareProfile.STANDARD
        assert config.primary_model == "qwen3:8b"
        assert config.dialog_recorder_model == "qwen3:1.7b"
        assert config.dialog_recorder_mode == DialogRecorderMode.SEQUENTIAL

    def test_performance_profile(self):
        """Verify performance hardware selects performance."""
        hw = HardwareInfo(gpu_vram_gb=24.0, ram_gb=32.0, cpu_cores=16)
        config = select_profile(hw)
        assert config.profile == HardwareProfile.PERFORMANCE
        assert config.primary_model == "qwen3:14b"
        assert config.dialog_recorder_model == "qwen3:4b"
        assert config.dialog_recorder_mode == DialogRecorderMode.PARALLEL

    def test_maximal_profile(self):
        """Verify maximal hardware selects maximal profile."""
        hw = HardwareInfo(gpu_vram_gb=48.0, ram_gb=64.0, cpu_cores=32)
        config = select_profile(hw)
        assert config.profile == HardwareProfile.MAXIMAL
        assert config.primary_model == "qwen3:32b"
        assert config.dialog_recorder_model == "qwen3:8b"
        assert config.dialog_recorder_mode == DialogRecorderMode.PARALLEL
        assert config.distiller_parallel

    def test_no_gpu_selects_minimal(self):
        """Verify no GPU defaults to minimal profile."""
        hw = HardwareInfo(gpu_vram_gb=0.0, ram_gb=8.0, cpu_cores=4)
        config = select_profile(hw)
        assert config.profile == HardwareProfile.MINIMAL

    def test_between_standard_and_performance(self):
        """Verify hardware between tiers selects lower."""
        hw = HardwareInfo(gpu_vram_gb=16.0, ram_gb=24.0, cpu_cores=12)
        config = select_profile(hw)
        assert config.profile == HardwareProfile.STANDARD
