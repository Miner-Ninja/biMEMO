"""Master key runtime protection with memory locking and zeroization.

Protects the plaintext master key in RAM from leaking to persistent
storage or memory dumps. Uses mlock() to prevent swapping, MADV_DONTDUMP
to exclude from core dumps, and prctl to prevent /proc/pid/mem access.
Registers a SIGTERM handler for graceful key zeroization on shutdown.
"""

import ctypes
import ctypes.util
import logging
import signal
import sys


logger = logging.getLogger(__name__)

# --- Key size ---
MASTER_KEY_LENGTH = 32


class MasterKeyHolder:
    """Holds and protects the master key in memory.

    Attributes:
        key: The master key as a mutable bytearray, or None if not set.
    """

    def __init__(self) -> None:
        """Initialize empty key holder."""
        self.key: bytearray | None = None
        self._original_sigterm: signal.Handlers | None = None

    def store(self, key_bytes: bytes) -> None:
        """Store master key with runtime memory protections.

        Copies the key into a mutable bytearray, applies mlock,
        MADV_DONTDUMP, and PR_SET_DUMPABLE protections, and registers
        a SIGTERM handler for zeroization.

        Args:
            key_bytes: The 32-byte master key.

        Raises:
            ValueError: If key is not exactly 32 bytes.
        """
        if len(key_bytes) != MASTER_KEY_LENGTH:
            msg = f"Master key must be {MASTER_KEY_LENGTH} bytes, got {len(key_bytes)}"
            raise ValueError(msg)

        self.key = bytearray(key_bytes)
        self._apply_memory_protections()
        self._register_sigterm_handler()
        logger.info("Master key stored with runtime protections")

    def _apply_memory_protections(self) -> None:
        """Apply OS-level memory protections to the key buffer."""
        if self.key is None:
            return

        if not self._can_apply_memory_protections():
            return

        libc = _get_libc()
        if libc is None:
            logger.warning("Could not load libc for memory protections")
            return

        page_start, region_size = self._protected_region()

        self._apply_mlock(libc, page_start, region_size)
        self._apply_madvise(libc, page_start, region_size)
        self._apply_prctl(libc)

    def _can_apply_memory_protections(self) -> bool:
        """Return whether Linux-only protections can be applied here."""
        if sys.platform != "linux":
            logger.warning("Memory protections only available on Linux")
            return False
        return True

    def _protected_region(self) -> tuple[int, int]:
        """Return the page-aligned region covering the key buffer."""
        assert self.key is not None
        key_address = self._key_buffer_address()
        page_size = self._page_size()
        page_start = key_address & ~(page_size - 1)
        page_end = (key_address + len(self.key) + page_size - 1) & ~(page_size - 1)
        return page_start, page_end - page_start

    def _key_buffer_address(self) -> int:
        """Return the address of the current key buffer."""
        assert self.key is not None
        return ctypes.addressof(self._key_buffer())

    def _key_buffer(self) -> ctypes.Array[ctypes.c_char]:
        """Return the ctypes view over the current mutable key buffer."""
        assert self.key is not None
        return (ctypes.c_char * len(self.key)).from_buffer(self.key)

    @staticmethod
    def _page_size() -> int:
        """Return the page size used for memory protection alignment."""
        return 4096

    @staticmethod
    def _apply_mlock(libc: ctypes.CDLL, page_start: int, region_size: int) -> None:
        """Prevent the protected memory region from being swapped out."""
        result = libc.mlock(ctypes.c_void_p(page_start), ctypes.c_size_t(region_size))
        if result != 0:
            logger.warning("mlock() failed — key may be swappable")

    @staticmethod
    def _apply_madvise(libc: ctypes.CDLL, page_start: int, region_size: int) -> None:
        """Exclude the protected region from core dumps."""
        madv_dontdump = 16
        result = libc.madvise(
            ctypes.c_void_p(page_start),
            ctypes.c_size_t(region_size),
            ctypes.c_int(madv_dontdump),
        )
        if result != 0:
            logger.warning("madvise(MADV_DONTDUMP) failed")

    @staticmethod
    def _apply_prctl(libc: ctypes.CDLL) -> None:
        """Prevent /proc/pid/mem access to this process."""
        pr_set_dumpable = 4
        result = libc.prctl(ctypes.c_int(pr_set_dumpable), ctypes.c_ulong(0), 0, 0, 0)
        if result != 0:
            logger.warning("prctl(PR_SET_DUMPABLE, 0) failed")

    def _register_sigterm_handler(self) -> None:
        """Register SIGTERM handler for graceful key zeroization."""
        self._original_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, self._sigterm_handler)

    def _sigterm_handler(self, signum: int, frame: object) -> None:
        """Handle SIGTERM by zeroing the key and exiting.

        Args:
            signum: Signal number.
            frame: Current stack frame.
        """
        logger.info("SIGTERM received — zeroing master key")
        self.zeroize()
        if callable(self._original_sigterm):
            self._original_sigterm(signum, frame)
        sys.exit(0)

    def zeroize(self) -> None:
        """Overwrite the key with zeroes."""
        if self.key is not None:
            self._zeroize_buffer(self.key)
            self.key = None
            logger.info("Master key zeroized")

    @staticmethod
    def _zeroize_buffer(key: bytearray) -> None:
        """Overwrite one mutable buffer with zero bytes."""
        key_buffer = (ctypes.c_char * len(key)).from_buffer(key)
        ctypes.memset(
            ctypes.addressof(key_buffer),
            0,
            len(key),
        )


def _get_libc() -> ctypes.CDLL | None:
    """Load libc for system calls.

    Returns:
        Loaded libc or None if unavailable.
    """
    libc_name = ctypes.util.find_library("c")
    if libc_name is None:
        return None
    return ctypes.CDLL(libc_name, use_errno=True)
