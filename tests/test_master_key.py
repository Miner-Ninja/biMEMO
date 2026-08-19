"""Tests for master key runtime protection.

Covers bytearray storage, key zeroization, value validation,
and SIGTERM handler registration.
"""

import signal

import pytest

from backend.security.master_key import MASTER_KEY_LENGTH, MasterKeyHolder


class TestMasterKeyStorage:
    """Tests for master key bytearray storage."""

    def test_store_creates_bytearray(self):
        """Verify key is stored as bytearray."""
        holder = MasterKeyHolder()
        key = b"\x01" * MASTER_KEY_LENGTH
        holder.store(key)
        assert isinstance(holder.key, bytearray)
        assert holder.key == bytearray(key)
        holder.zeroize()

    def test_store_wrong_length_raises(self):
        """Verify wrong key length raises ValueError."""
        holder = MasterKeyHolder()
        with pytest.raises(ValueError, match="32 bytes"):
            holder.store(b"\x01" * 16)

    def test_store_copies_key(self):
        """Verify key is copied, not referenced."""
        holder = MasterKeyHolder()
        original = b"\xab" * MASTER_KEY_LENGTH
        holder.store(original)
        assert holder.key is not original
        holder.zeroize()


class TestZeroization:
    """Tests for key zeroization."""

    def test_zeroize_clears_key(self):
        """Verify zeroize sets key to None."""
        holder = MasterKeyHolder()
        holder.store(b"\xff" * MASTER_KEY_LENGTH)
        holder.zeroize()
        assert holder.key is None

    def test_zeroize_on_none_is_safe(self):
        """Verify zeroize on empty holder does not raise."""
        holder = MasterKeyHolder()
        holder.zeroize()


class TestSIGTERMHandler:
    """Tests for SIGTERM handler registration."""

    def test_sigterm_handler_registered(self):
        """Verify SIGTERM handler is registered after store."""
        holder = MasterKeyHolder()
        holder.store(b"\x01" * MASTER_KEY_LENGTH)
        current_handler = signal.getsignal(signal.SIGTERM)
        assert current_handler == holder._sigterm_handler
        holder.zeroize()
        # Restore default handler
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

    def test_sigterm_handler_zeroizes_key(self):
        """Verify SIGTERM handler zeroizes the key."""
        holder = MasterKeyHolder()
        holder.store(b"\x01" * MASTER_KEY_LENGTH)
        with pytest.raises(SystemExit):
            holder._sigterm_handler(signal.SIGTERM, None)
        assert holder.key is None
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

    def test_sigterm_calls_original_handler(self):
        """Verify SIGTERM handler calls previous handler."""
        called = []

        def original_handler(signum, frame):
            called.append(signum)

        signal.signal(signal.SIGTERM, original_handler)
        holder = MasterKeyHolder()
        holder.store(b"\x01" * MASTER_KEY_LENGTH)
        with pytest.raises(SystemExit):
            holder._sigterm_handler(signal.SIGTERM, None)
        assert signal.SIGTERM in called
        signal.signal(signal.SIGTERM, signal.SIG_DFL)


class TestMemoryProtections:
    """Tests for OS-level memory protections."""

    def test_apply_protections_on_none_key(self):
        """Verify protections are skipped when key is None."""
        holder = MasterKeyHolder()
        holder._apply_memory_protections()

    def test_apply_protections_on_non_linux(self):
        """Verify protections warn on non-Linux."""
        from unittest.mock import patch

        holder = MasterKeyHolder()
        holder.key = bytearray(b"\x01" * MASTER_KEY_LENGTH)
        with patch("sys.platform", "darwin"):
            holder._apply_memory_protections()
        holder.zeroize()

    def test_apply_protections_no_libc(self):
        """Verify protections warn when libc is unavailable."""
        from unittest.mock import patch

        holder = MasterKeyHolder()
        holder.key = bytearray(b"\x01" * MASTER_KEY_LENGTH)
        with patch(
            "backend.security.master_key._get_libc",
            return_value=None,
        ):
            holder._apply_memory_protections()
        holder.zeroize()

    def test_apply_protections_mlock_failure(self):
        """Verify mlock failure logs a warning."""
        from unittest.mock import MagicMock, patch

        holder = MasterKeyHolder()
        holder.key = bytearray(b"\x01" * MASTER_KEY_LENGTH)
        mock_libc = MagicMock()
        mock_libc.mlock.return_value = -1
        mock_libc.madvise.return_value = 0
        mock_libc.prctl.return_value = 0
        with patch(
            "backend.security.master_key._get_libc",
            return_value=mock_libc,
        ):
            holder._apply_memory_protections()
        holder.zeroize()

    def test_apply_protections_madvise_failure(self):
        """Verify madvise failure logs a warning."""
        from unittest.mock import MagicMock, patch

        holder = MasterKeyHolder()
        holder.key = bytearray(b"\x01" * MASTER_KEY_LENGTH)
        mock_libc = MagicMock()
        mock_libc.mlock.return_value = 0
        mock_libc.madvise.return_value = -1
        mock_libc.prctl.return_value = 0
        with patch(
            "backend.security.master_key._get_libc",
            return_value=mock_libc,
        ):
            holder._apply_memory_protections()
        holder.zeroize()

    def test_apply_protections_prctl_failure(self):
        """Verify prctl failure logs a warning."""
        from unittest.mock import MagicMock, patch

        holder = MasterKeyHolder()
        holder.key = bytearray(b"\x01" * MASTER_KEY_LENGTH)
        mock_libc = MagicMock()
        mock_libc.mlock.return_value = 0
        mock_libc.madvise.return_value = 0
        mock_libc.prctl.return_value = -1
        with patch(
            "backend.security.master_key._get_libc",
            return_value=mock_libc,
        ):
            holder._apply_memory_protections()
        holder.zeroize()


class TestGetLibc:
    """Tests for _get_libc helper."""

    def test_get_libc_returns_cdll(self):
        """Verify _get_libc returns a loaded library."""
        import ctypes
        import sys

        from backend.security.master_key import _get_libc

        if sys.platform == "linux":
            result = _get_libc()
            assert isinstance(result, ctypes.CDLL)

    def test_get_libc_none_when_unavailable(self):
        """Verify _get_libc returns None when library not found."""
        from unittest.mock import patch

        from backend.security.master_key import _get_libc

        with patch("ctypes.util.find_library", return_value=None):
            assert _get_libc() is None
