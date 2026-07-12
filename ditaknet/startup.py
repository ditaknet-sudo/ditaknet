"""
Startup validation for Docker, TrueNAS, and production deployments.

Runs before the database opens so misconfigured volumes or secrets fail early
with a clear error instead of partial startup.
"""

from __future__ import annotations

from ditaknet.config import settings
from ditaknet.utils.paths import directory_status, ensure_directory


WEAK_PASSWORDS = frozenset({"", "change-me", "changeme", "admin", "password"})


def _looks_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    return (
        lowered in WEAK_PASSWORDS
        or lowered.startswith("change_me")
        or lowered.startswith("change-me")
        or "replace" in lowered
    )


class StartupError(RuntimeError):
    """Raised when mandatory startup checks fail in production."""


def validate_production_settings() -> None:
    """Fail fast when production configuration is unsafe.

    Session signing uses a generated persistent key when no external secret is
    supplied. Admin passwords are stored only as hashes in the database.
    """
    if settings.app_env.lower() != "production":
        return

    secret = settings.effective_secret_key.strip()
    if _looks_placeholder(secret):
        raise StartupError(
            "SECRET_KEY (or SESSION_SECRET) must be set to a strong random value in production."
        )

    if settings.database_type != "sqlite":
        raise StartupError(
            "Only SQLite DATABASE_URL values are supported in this release. "
            "Use sqlite:////app/data/ditaknet.db for Docker/TrueNAS."
        )


def prepare_runtime_directories() -> dict:
    """Ensure data/backup/log directories exist and report writability."""
    data_dir = ensure_directory(settings.data_dir_path)
    backup_dir = ensure_directory(settings.backup_dir_path)
    log_dir = ensure_directory(settings.log_dir_path)
    ensure_directory(settings.db_path.parent)

    return {
        "data": directory_status(data_dir),
        "backups": directory_status(backup_dir),
        "logs": directory_status(log_dir),
    }


def validate_writable_directories(status: dict) -> None:
    """Raise if required directories are not writable."""
    failures = [name for name, info in status.items() if not info.get("ok")]
    if failures:
        details = ", ".join(f"{name}={status[name]['path']}" for name in failures)
        raise StartupError(f"Required directories are missing or not writable: {details}")
