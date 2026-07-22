"""Cross-process ownership lock for the writable DitakNet data directory.

The web service holds this lock for its whole lifetime. Destructive maintenance
commands must acquire the same lock, which makes an offline restore fail closed
while any lock-aware DitakNet process still owns the mounted database directory.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO


LOCK_FILENAME = ".ditaknet-runtime.lock"


class RuntimeLockError(RuntimeError):
    """Raised when another process owns the DitakNet runtime lock."""


class RuntimeLock:
    def __init__(self, path: Path, handle: BinaryIO) -> None:
        self.path = path
        self._handle = handle
        self._released = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            self._unlock()
        finally:
            self._handle.close()

    def _unlock(self) -> None:
        if os.name == "nt":
            import msvcrt

            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            return

        import fcntl

        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)

    def __enter__(self) -> RuntimeLock:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()

    def __del__(self) -> None:  # pragma: no cover - interpreter safety net
        try:
            self.release()
        except Exception:
            pass


def runtime_lock_path(data_directory: str | Path) -> Path:
    return Path(data_directory).expanduser().resolve() / LOCK_FILENAME


def acquire_runtime_lock(data_directory: str | Path) -> RuntimeLock:
    """Acquire exclusive non-blocking ownership of one runtime data directory."""

    path = runtime_lock_path(data_directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeLockError(
            "DitakNet is still running against this data directory; stop every "
            "container/process before offline maintenance"
        ) from exc
    return RuntimeLock(path, handle)
