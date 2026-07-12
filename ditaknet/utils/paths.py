"""Filesystem path helpers for deployment directories."""

from __future__ import annotations

from pathlib import Path


def ensure_directory(path: Path) -> Path:
    """Create a directory if missing and return the resolved path."""
    resolved = path.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def directory_status(path: Path) -> dict:
    """Return existence and writability status for a directory.

    Uses a real write probe so TrueNAS/Docker mount permission issues are
    detected even when ``os.access`` is misleading.
    """
    resolved = path.expanduser().resolve()
    exists = resolved.exists()
    writable = False
    error: str | None = None

    if not exists:
        try:
            resolved.mkdir(parents=True, exist_ok=True)
            exists = True
        except OSError as exc:
            error = f"cannot create directory: {exc}"
            return {
                "path": str(resolved),
                "exists": False,
                "writable": False,
                "ok": False,
                "error": error,
            }

    probe = resolved / ".ditaknet_write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        writable = True
    except OSError as exc:
        writable = False
        error = f"not writable: {exc}"

    return {
        "path": str(resolved),
        "exists": exists,
        "writable": writable,
        "ok": exists and writable,
        "error": error,
    }
