"""Filesystem path helpers for deployment directories."""

from __future__ import annotations

import os
from pathlib import Path


def ensure_directory(path: Path) -> Path:
    """Create a directory if missing and return the resolved path."""
    resolved = path.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def directory_status(path: Path) -> dict:
    """Return existence and writability status for a directory."""
    resolved = path.expanduser().resolve()
    exists = resolved.exists()
    writable = False
    if exists:
        writable = os.access(resolved, os.W_OK)
    else:
        try:
            resolved.mkdir(parents=True, exist_ok=True)
            exists = True
            writable = os.access(resolved, os.W_OK)
        except OSError:
            exists = False
            writable = False
    return {
        "path": str(resolved),
        "exists": exists,
        "writable": writable,
        "ok": exists and writable,
    }
