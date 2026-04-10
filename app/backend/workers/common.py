"""Shared worker subprocess utilities.

Common entry-point logic used by both FS and Net workers.
"""

import multiprocessing.connection
import signal
import time


def _sleep_forever() -> None:
    """Sleep forever on platforms without signal.pause()."""
    while True:
        time.sleep(3600)


def _block_until_signal() -> None:
    """Block the worker on Unix-like platforms."""
    signal.pause()


def _supports_signal_pause() -> bool:
    """Return whether the current platform exposes signal.pause()."""
    return hasattr(signal, "pause")


def _wait_forever() -> None:
    """Sleep forever on platforms without signal.pause()."""
    if _supports_signal_pause():
        _block_until_signal()
        return
    _sleep_forever()


def worker_main(conn: multiprocessing.connection.Connection) -> None:
    """Common subprocess entry point.

    Reads the shared secret from the parent process via pipe,
    then blocks until terminated by the parent.

    Args:
        conn: Pipe connection for receiving the shared secret.
    """
    conn.recv_bytes()
    conn.close()
    _wait_forever()
