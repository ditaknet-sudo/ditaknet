"""Full and database-only backup helpers with ZIP manifest support."""

from __future__ import annotations

import json
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ditaknet import database as db
from ditaknet.config import settings
from ditaknet.core.system_log_service import redact_metadata, redact_text

BACKUP_SUFFIX = ".sqlite3"
ZIP_SUFFIX = ".zip"
FORMAT_VERSION = 1
AUTO_BACKUP_PREFIX = "ditaknet-auto-backup-"
MANUAL_BACKUP_PREFIX = "ditaknet-backup-"

SENSITIVE_SETTING_KEYS = frozenset(
    {
        "telegram_bot_token",
        "telegram_chat_id",
        "license_private_key",
        "agent_registration_key",
    }
)


def backup_root() -> Path:
    root = settings.backup_dir_path
    root.mkdir(parents=True, exist_ok=True)
    return root


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def format_size(size_bytes: int) -> str:
    size = float(max(0, size_bytes))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


def resolve_backup_path(name: str | None = None, *, zip_backup: bool = True) -> Path:
    root = backup_root()
    filename = name or f"ditaknet-backup-{_timestamp()}{ZIP_SUFFIX if zip_backup else BACKUP_SUFFIX}"
    candidate = Path(filename)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        raise ValueError("Invalid backup path")
    suffix = candidate.suffix.lower()
    if suffix not in {ZIP_SUFFIX, BACKUP_SUFFIX, ".db", ".sqlite"}:
        candidate = candidate.with_suffix(ZIP_SUFFIX if zip_backup else BACKUP_SUFFIX)
    resolved = (root / candidate.name).resolve()
    if root != resolved and root not in resolved.parents:
        raise ValueError("Backup path escapes backup directory")
    return resolved


def database_path() -> Path:
    try:
        return db.get_db_path().expanduser().resolve()
    except Exception:
        return settings.db_path


async def _table_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in (
        "hosts",
        "services",
        "check_results",
        "alerts",
        "discovered_devices",
        "monitored_networks",
        "users",
        "roles",
        "licenses",
        "employees",
        "audit_logs",
        "system_logs",
    ):
        try:
            connection = await db.get_db()
            rows = await connection.execute_fetchall(f"SELECT COUNT(*) AS cnt FROM {table}")
            counts[table] = int(rows[0]["cnt"] if rows else 0)
        except Exception:
            counts[table] = 0
    return counts


async def _export_settings_safe() -> dict[str, str]:
    raw = await db.list_all_app_settings()
    safe: dict[str, str] = {}
    for key, value in raw.items():
        if key in SENSITIVE_SETTING_KEYS:
            safe[key] = "[REDACTED]"
        elif "password" in key.lower() or "token" in key.lower() or "secret" in key.lower():
            safe[key] = "[REDACTED]"
        else:
            safe[key] = redact_text(value)
    return safe


async def _export_license_safe() -> dict[str, Any]:
    try:
        from ditaknet.core.licensing import license_service

        status = await license_service.status()
        return redact_metadata(
            {
                "tier": status.get("tier"),
                "valid": status.get("valid"),
                "expires_at": status.get("expires_at"),
                "package": status.get("package"),
                "features": status.get("features"),
            }
        )
    except Exception as exc:
        return {"error": type(exc).__name__}


def _build_manifest(
    *,
    backup_type: str,
    includes: list[str],
    table_counts: dict[str, int],
    backup_origin: str = "manual",
) -> dict[str, Any]:
    return {
        "format": "ditaknet-backup",
        "format_version": FORMAT_VERSION,
        "backup_type": backup_type,
        "backup_origin": backup_origin,
        "app_version": settings.app_version,
        "build_commit": settings.build_commit.strip() or None,
        "build_date": settings.release_build_date or None,
        "image_tag": settings.image_tag.strip() or None,
        "created_at": datetime.now(UTC).isoformat(),
        "includes": includes,
        "table_counts": table_counts,
    }


def create_backup(name: str | None = None) -> dict:
    """Create a database-only backup (legacy)."""
    source = database_path()
    if not source.exists():
        raise FileNotFoundError("Database file does not exist")
    target = resolve_backup_path(name, zip_backup=False)
    shutil.copy2(source, target)
    for suffix in ("-wal", "-shm"):
        sidecar = source.with_name(source.name + suffix)
        if sidecar.exists():
            shutil.copy2(sidecar, target.with_name(target.name + suffix))
    return _serialize_backup_file(target, backup_type="database")


def is_automatic_backup_name(name: str) -> bool:
    base = Path(name).name
    return base.startswith(AUTO_BACKUP_PREFIX)


def backup_origin_for_file(path: Path, manifest: dict | None = None) -> str:
    if manifest and manifest.get("backup_origin"):
        return str(manifest["backup_origin"])
    if is_automatic_backup_name(path.name):
        return "automatic"
    return "manual"


async def create_full_backup(
    name: str | None = None,
    *,
    include_logs: bool = False,
    backup_origin: str = "manual",
) -> dict:
    """Create ZIP backup with manifest, database, settings, and license metadata."""
    source = database_path()
    if not source.exists():
        raise FileNotFoundError("Database file does not exist")

    target = resolve_backup_path(name, zip_backup=True)
    table_counts = await _table_counts()
    manifest = _build_manifest(
        backup_type="full",
        includes=["database", "users", "roles", "settings", "license", "audit_logs", "metadata"],
        table_counts=table_counts,
        backup_origin=backup_origin,
    )
    settings_export = await _export_settings_safe()
    license_export = await _export_license_safe()
    metadata = {
        "deployment_mode": settings.app_deployment_mode or None,
        "database_type": settings.database_type,
        "database_path": str(source),
    }

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(source, arcname="database.sqlite3")
        zf.writestr("backup.json", json.dumps(manifest, indent=2))
        zf.writestr("settings.json", json.dumps(settings_export, indent=2))
        zf.writestr("license.json", json.dumps(license_export, indent=2))
        zf.writestr("metadata.json", json.dumps(metadata, indent=2))
        if include_logs and settings.log_dir_path.exists():
            for log_file in sorted(settings.log_dir_path.glob("*.log"))[:20]:
                if log_file.is_file():
                    zf.write(log_file, arcname=f"logs/{log_file.name}")

    return _serialize_backup_file(target, backup_type="full", manifest=manifest)


async def create_automatic_full_backup() -> dict:
    """Create a scheduled automatic backup and enforce retention policy."""
    target_name = f"{AUTO_BACKUP_PREFIX}{_timestamp()}{ZIP_SUFFIX}"
    backup = await create_full_backup(target_name, backup_origin="automatic")
    pruned = prune_automatic_backups()
    backup["pruned"] = pruned
    return backup


def prune_automatic_backups(keep: int | None = None) -> list[str]:
    """Delete oldest automatic backups beyond retention; manual backups are preserved."""
    keep_count = keep if keep is not None else settings.auto_backup_keep_count
    keep_count = max(1, int(keep_count))
    automatic: list[Path] = []
    for entry in list_backups():
        filename = str(entry.get("filename") or "")
        if entry.get("backup_origin") == "automatic" or is_automatic_backup_name(filename):
            automatic.append(backup_root() / filename)
    automatic.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    deleted: list[str] = []
    for path in automatic[keep_count:]:
        delete_backup(path.name)
        deleted.append(path.name)
    return deleted


async def run_weekly_automatic_backup() -> dict:
    """Scheduler entrypoint for Sunday automatic backups."""
    if not settings.auto_backup_enabled:
        return {"skipped": True, "reason": "auto_backup_disabled"}
    backup = await create_automatic_full_backup()
    try:
        from ditaknet.core.notifications_service import notify_backup_result

        await notify_backup_result(success=True, filename=backup["filename"])
    except Exception:
        pass
    return backup


def _serialize_backup_file(path: Path, *, backup_type: str, manifest: dict | None = None) -> dict:
    stat = path.stat()
    summary = []
    if manifest:
        counts = manifest.get("table_counts") or {}
        if counts.get("hosts"):
            summary.append(f"{counts['hosts']} hosts")
        if counts.get("services"):
            summary.append(f"{counts['services']} services")
        if counts.get("check_results"):
            summary.append(f"{counts['check_results']} checks")
    return {
        "filename": path.name,
        "path": str(path),
        "backup_type": backup_type,
        "app_version": (manifest or {}).get("app_version") or settings.app_version,
        "size_bytes": stat.st_size,
        "size_display": format_size(stat.st_size),
        "created_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        "includes_summary": ", ".join(summary) if summary else backup_type,
        "status": "ready",
        "backup_origin": backup_origin_for_file(path, manifest),
        "manifest": manifest,
    }


def list_backups() -> list[dict]:
    root = backup_root()
    backups: list[dict] = []
    for path in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {ZIP_SUFFIX, BACKUP_SUFFIX, ".db", ".sqlite"}:
            continue
        manifest = None
        backup_type = "full" if path.suffix.lower() == ZIP_SUFFIX else "database"
        if path.suffix.lower() == ZIP_SUFFIX:
            try:
                with zipfile.ZipFile(path) as zf:
                    if "backup.json" in zf.namelist():
                        manifest = json.loads(zf.read("backup.json"))
                        backup_type = manifest.get("backup_type", "full")
            except Exception:
                backup_type = "full"
        backups.append(_serialize_backup_file(path, backup_type=backup_type, manifest=manifest))
    return backups


def validate_backup_file(name: str) -> dict[str, Any]:
    path = resolve_backup_path(name)
    if not path.exists():
        raise FileNotFoundError("Backup not found")
    if path.suffix.lower() == ZIP_SUFFIX:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if "database.sqlite3" not in names:
                raise ValueError("Backup ZIP missing database.sqlite3")
            manifest = {}
            if "backup.json" in names:
                manifest = json.loads(zf.read("backup.json"))
            return {
                "valid": True,
                "filename": path.name,
                "backup_type": manifest.get("backup_type", "full"),
                "format_version": manifest.get("format_version"),
                "app_version": manifest.get("app_version"),
                "created_at": manifest.get("created_at"),
                "includes": manifest.get("includes", []),
                "table_counts": manifest.get("table_counts", {}),
            }
    if path.stat().st_size < 1024:
        raise ValueError("Database backup file is too small")
    return {
        "valid": True,
        "filename": path.name,
        "backup_type": "database",
        "app_version": settings.app_version,
        "includes": ["database"],
    }


def delete_backup(name: str) -> None:
    path = resolve_backup_path(name)
    if not path.exists():
        raise FileNotFoundError("Backup not found")
    path.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        sidecar.unlink(missing_ok=True)
