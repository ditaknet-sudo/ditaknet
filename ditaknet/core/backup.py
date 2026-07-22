"""Full and database-only backup helpers with ZIP manifest support."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ditaknet import database as db
from ditaknet.config import settings
from ditaknet.core.system_log_service import redact_metadata, redact_text

BACKUP_SUFFIX = ".sqlite3"
ZIP_SUFFIX = ".zip"
FORMAT_VERSION = 2
AUTO_BACKUP_PREFIX = "ditaknet-auto-backup-"
MANUAL_BACKUP_PREFIX = "ditaknet-backup-"
MAX_BACKUP_FILE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ZIP_UNCOMPRESSED_BYTES = 8 * 1024 * 1024 * 1024
MAX_ZIP_MEMBER_BYTES = 8 * 1024 * 1024 * 1024
MAX_ZIP_MEMBER_COUNT = 256
MAX_ZIP_COMPRESSION_RATIO = 200
MAX_BACKUP_MANIFEST_BYTES = 256 * 1024
MAX_BACKUP_SETTINGS_BYTES = 8 * 1024 * 1024
MAX_BACKUP_LICENSE_BYTES = 2 * 1024 * 1024
MAX_BACKUP_METADATA_BYTES = 1 * 1024 * 1024
MAX_BACKUP_LOG_MEMBER_BYTES = 64 * 1024 * 1024
MAX_BACKUP_OTHER_MEMBER_BYTES = 64 * 1024 * 1024
ZIP_STREAM_CHUNK_BYTES = 1024 * 1024
BACKUP_OPERATION_LOCK = asyncio.Lock()

SENSITIVE_SETTING_KEYS = frozenset(
    {
        "telegram_bot_token",
        "telegram_chat_id",
        "license_private_key",
        "agent_registration_key",
    }
)

_REQUIRED_DATABASE_TABLES = frozenset({"hosts", "services", "app_settings"})


def _validate_sqlite_database(path: Path) -> None:
    """Reject corrupt SQLite files and databases that are not DitakNet backups."""
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError("Backup database is empty or missing")

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(str(path))
        connection.execute("PRAGMA query_only=ON")
        integrity_rows = connection.execute("PRAGMA quick_check").fetchall()
        if not integrity_rows or any(
            str(row[0]).lower() != "ok" for row in integrity_rows
        ):
            raise ValueError("Backup database integrity check failed")
        foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_rows:
            raise ValueError("Backup database foreign-key check failed")
        table_rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        tables = {str(row[0]) for row in table_rows}
        missing = sorted(_REQUIRED_DATABASE_TABLES - tables)
        if missing:
            raise ValueError(
                f"Backup is not a DitakNet database; missing tables: {', '.join(missing)}"
            )
    except sqlite3.DatabaseError as exc:
        raise ValueError("Backup database is not a valid SQLite database") from exc
    finally:
        if connection is not None:
            connection.close()


def _create_sqlite_snapshot(source: Path, target: Path) -> None:
    """Create one consistent SQLite file, including committed WAL contents."""
    if not source.is_file():
        raise FileNotFoundError("Database file does not exist")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)

    source_connection: sqlite3.Connection | None = None
    target_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(str(source))
        target_connection = sqlite3.connect(str(target))
        source_connection.backup(target_connection)
        target_connection.commit()
    except sqlite3.DatabaseError as exc:
        target.unlink(missing_ok=True)
        raise ValueError("Could not create a consistent SQLite backup") from exc
    finally:
        if target_connection is not None:
            target_connection.close()
        if source_connection is not None:
            source_connection.close()

    _validate_sqlite_database(target)


def _validate_backup_manifest(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest, dict):
        raise ValueError("Backup manifest must be a JSON object")
    manifest_format = str(manifest.get("format") or "").strip()
    if manifest_format and manifest_format != "ditaknet-backup":
        raise ValueError("Unsupported backup manifest format")
    format_version = manifest.get("format_version")
    parsed_version = 1
    if format_version is not None:
        if isinstance(format_version, bool):
            raise ValueError("Invalid backup format version")
        try:
            parsed_version = int(format_version)
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid backup format version") from exc
        if parsed_version < 1 or parsed_version > FORMAT_VERSION:
            raise ValueError(f"Unsupported backup format version: {parsed_version}")
    if parsed_version >= 2 and manifest_format != "ditaknet-backup":
        raise ValueError("Backup format v2 requires the DitakNet manifest marker")
    manifest["format_version"] = parsed_version
    return manifest


def _zip_member_size_limit(member: str) -> int:
    limits = {
        "backup.json": MAX_BACKUP_MANIFEST_BYTES,
        "settings.json": MAX_BACKUP_SETTINGS_BYTES,
        "license.json": MAX_BACKUP_LICENSE_BYTES,
        "metadata.json": MAX_BACKUP_METADATA_BYTES,
        "database.sqlite3": MAX_ZIP_MEMBER_BYTES,
    }
    if member.startswith("logs/"):
        return MAX_BACKUP_LOG_MEMBER_BYTES
    return limits.get(member, MAX_BACKUP_OTHER_MEMBER_BYTES)


def _read_zip_member_bounded(zf: zipfile.ZipFile, member: str) -> bytes:
    """Read a small ZIP control member without trusting its declared size."""
    limit = _zip_member_size_limit(member)
    info = zf.getinfo(member)
    if info.file_size > limit:
        raise ValueError(f"Backup ZIP member is too large: {member}")

    data = bytearray()
    with zf.open(info) as source:
        while chunk := source.read(min(ZIP_STREAM_CHUNK_BYTES, limit + 1 - len(data))):
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError(f"Backup ZIP member is too large: {member}")
    return bytes(data)


def _read_backup_manifest(zf: zipfile.ZipFile) -> dict[str, Any]:
    try:
        payload = _read_zip_member_bounded(zf, "backup.json")
        return _validate_backup_manifest(json.loads(payload))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Backup manifest is not valid JSON") from exc


def _zip_member_sha256(zf: zipfile.ZipFile, member: str) -> str:
    digest = hashlib.sha256()
    limit = _zip_member_size_limit(member)
    total = 0
    with zf.open(member) as source:
        for chunk in iter(lambda: source.read(ZIP_STREAM_CHUNK_BYTES), b""):
            total += len(chunk)
            if total > limit:
                raise ValueError(f"Backup ZIP member is too large: {member}")
            digest.update(chunk)
    return digest.hexdigest()


def _validate_zip_structure(path: Path, zf: zipfile.ZipFile) -> list[str]:
    if path.stat().st_size > MAX_BACKUP_FILE_BYTES:
        raise ValueError("Backup file exceeds the supported size limit")
    entries = zf.infolist()
    if len(entries) > MAX_ZIP_MEMBER_COUNT:
        raise ValueError("Backup ZIP contains too many members")
    names = [entry.filename for entry in entries]
    if len(names) != len(set(names)):
        raise ValueError("Backup ZIP contains duplicate member names")

    total_uncompressed = 0
    for entry in entries:
        member_path = Path(entry.filename)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise ValueError("Backup ZIP contains an unsafe member path")
        if entry.file_size > _zip_member_size_limit(entry.filename):
            raise ValueError(f"Backup ZIP member is too large: {entry.filename}")
        total_uncompressed += entry.file_size
        if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
            raise ValueError("Backup ZIP expands beyond the supported size limit")
        if (
            entry.file_size > 1024 * 1024
            and entry.file_size
            > max(1, entry.compress_size) * MAX_ZIP_COMPRESSION_RATIO
        ):
            raise ValueError(
                f"Backup ZIP member has an unsafe compression ratio: {entry.filename}"
            )
    return names


def backup_root() -> Path:
    root = settings.backup_dir_path
    root.mkdir(parents=True, exist_ok=True)
    return root


def _timestamp() -> str:
    # Microseconds avoid collisions when two administrative operations create
    # recovery points during the same second.
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")


def file_sha256(path: Path) -> str:
    """Return a streaming SHA-256 digest for a completed backup artifact."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def format_size(size_bytes: int) -> str:
    size = float(max(0, size_bytes))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


def resolve_backup_path(name: str | None = None, *, zip_backup: bool = True) -> Path:
    root = backup_root()
    filename = (
        name
        or f"ditaknet-backup-{_timestamp()}{ZIP_SUFFIX if zip_backup else BACKUP_SUFFIX}"
    )
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
            rows = await connection.execute_fetchall(
                f"SELECT COUNT(*) AS cnt FROM {table}"
            )
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
        elif (
            "password" in key.lower()
            or "token" in key.lower()
            or "secret" in key.lower()
        ):
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
    operation_context: dict[str, Any] | None = None,
    members_sha256: dict[str, str] | None = None,
) -> dict[str, Any]:
    manifest = {
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
        "members_sha256": members_sha256 or {},
    }
    if operation_context:
        allowed = {
            "source_version",
            "source_image_tag",
            "target_version",
            "target_image",
            "target_digest",
            "target_schema_revision",
            "rollback_policy",
            "update_channel",
            "manifest_sequence",
        }
        manifest["operation_context"] = {
            key: operation_context[key]
            for key in sorted(allowed)
            if key in operation_context and operation_context[key] is not None
        }
    return manifest


def create_backup(name: str | None = None) -> dict:
    """Create a database-only backup (legacy)."""
    source = database_path()
    if not source.exists():
        raise FileNotFoundError("Database file does not exist")
    target = resolve_backup_path(name, zip_backup=False)
    if target.exists():
        raise FileExistsError(f"Backup already exists: {target.name}")
    with tempfile.TemporaryDirectory(
        prefix=".ditaknet-db-backup-", dir=target.parent
    ) as tmp:
        snapshot = Path(tmp) / "database.sqlite3"
        _create_sqlite_snapshot(source, snapshot)
        os.link(snapshot, target)
    for suffix in ("-wal", "-shm"):
        # New backups are self-contained. Remove sidecars left by an older
        # backup with the same filename so they cannot shadow the snapshot.
        target.with_name(target.name + suffix).unlink(missing_ok=True)
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
    operation_context: dict[str, Any] | None = None,
) -> dict:
    """Create ZIP backup with manifest, database, settings, and license metadata."""
    source = database_path()
    if not source.exists():
        raise FileNotFoundError("Database file does not exist")

    target = resolve_backup_path(name, zip_backup=True)
    if target.exists():
        raise FileExistsError(f"Backup already exists: {target.name}")
    with tempfile.TemporaryDirectory(
        prefix=".ditaknet-full-backup-", dir=target.parent
    ) as tmp:
        tmp_path = Path(tmp)
        snapshot = tmp_path / "database.sqlite3"
        _create_sqlite_snapshot(source, snapshot)

        table_counts = await _table_counts()
        settings_export = await _export_settings_safe()
        license_export = await _export_license_safe()
        metadata = {
            "deployment_mode": settings.app_deployment_mode or None,
            "database_type": settings.database_type,
            "database_path": str(source),
        }

        settings_bytes = json.dumps(settings_export, indent=2).encode("utf-8")
        license_bytes = json.dumps(license_export, indent=2).encode("utf-8")
        metadata_bytes = json.dumps(metadata, indent=2).encode("utf-8")
        members_sha256 = {
            "database.sqlite3": file_sha256(snapshot),
            "settings.json": hashlib.sha256(settings_bytes).hexdigest(),
            "license.json": hashlib.sha256(license_bytes).hexdigest(),
            "metadata.json": hashlib.sha256(metadata_bytes).hexdigest(),
        }
        log_files: list[tuple[Path, str]] = []
        if include_logs and settings.log_dir_path.exists():
            for log_file in sorted(settings.log_dir_path.glob("*.log"))[:20]:
                if log_file.is_file():
                    archive_name = f"logs/{log_file.name}"
                    log_files.append((log_file, archive_name))
                    members_sha256[archive_name] = file_sha256(log_file)
        manifest = _build_manifest(
            backup_type="full",
            includes=[
                "database",
                "users",
                "roles",
                "settings",
                "license",
                "audit_logs",
                "metadata",
            ],
            table_counts=table_counts,
            backup_origin=backup_origin,
            operation_context=operation_context,
            members_sha256=members_sha256,
        )

        archive = tmp_path / target.name
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(snapshot, arcname="database.sqlite3")
            zf.writestr("backup.json", json.dumps(manifest, indent=2))
            zf.writestr("settings.json", settings_bytes)
            zf.writestr("license.json", license_bytes)
            zf.writestr("metadata.json", metadata_bytes)
            for log_file, archive_name in log_files:
                zf.write(log_file, arcname=archive_name)
        os.link(archive, target)

    # A backup is not a recovery point until the final artifact has passed the
    # same ZIP/member/SQLite validation used by restore. Remove a failed final
    # file so callers can never mistake it for a usable pre-update receipt.
    try:
        validation = validate_backup_file(target.name)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    result = _serialize_backup_file(target, backup_type="full", manifest=manifest)
    result["sha256"] = validation["sha256"]
    result["validated"] = True
    return result


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
        if entry.get("backup_origin") == "automatic" or is_automatic_backup_name(
            filename
        ):
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
    async with BACKUP_OPERATION_LOCK:
        backup = await create_automatic_full_backup()
    try:
        from ditaknet.core.notifications_service import notify_backup_result

        await notify_backup_result(success=True, filename=backup["filename"])
    except Exception:
        pass
    return backup


def _serialize_backup_file(
    path: Path, *, backup_type: str, manifest: dict | None = None
) -> dict:
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
                        manifest = _read_backup_manifest(zf)
                        backup_type = manifest.get("backup_type", "full")
            except Exception:
                backup_type = "full"
        backups.append(
            _serialize_backup_file(path, backup_type=backup_type, manifest=manifest)
        )
    return backups


def validate_backup_file(name: str) -> dict[str, Any]:
    path = resolve_backup_path(name)
    if not path.exists():
        raise FileNotFoundError("Backup not found")
    if path.stat().st_size > MAX_BACKUP_FILE_BYTES:
        raise ValueError("Backup file exceeds the supported size limit")
    if path.suffix.lower() == ZIP_SUFFIX:
        if not zipfile.is_zipfile(path):
            raise ValueError("Backup ZIP is invalid or corrupt")
        with zipfile.ZipFile(path) as zf:
            names = _validate_zip_structure(path, zf)
            if "database.sqlite3" not in names:
                raise ValueError("Backup ZIP missing database.sqlite3")
            bad_member = zf.testzip()
            if bad_member:
                raise ValueError(f"Backup ZIP contains a corrupt member: {bad_member}")
            manifest = {}
            if "backup.json" in names:
                manifest = _read_backup_manifest(zf)
            members_sha256 = manifest.get("members_sha256") or {}
            if not isinstance(members_sha256, dict):
                raise ValueError("Backup member checksums must be an object")
            if int(manifest.get("format_version") or 1) >= 2:
                archived_data_members = set(names) - {"backup.json"}
                checksummed_members = set(members_sha256)
                if checksummed_members != archived_data_members:
                    missing = sorted(archived_data_members - checksummed_members)
                    extra = sorted(checksummed_members - archived_data_members)
                    details: list[str] = []
                    if missing:
                        details.append(f"missing: {', '.join(missing)}")
                    if extra:
                        details.append(f"unknown: {', '.join(extra)}")
                    raise ValueError(
                        "Backup format v2 requires checksums for every data member"
                        + (f" ({'; '.join(details)})" if details else "")
                    )
            for member, expected in members_sha256.items():
                if member not in names:
                    raise ValueError(f"Backup ZIP missing checksummed member: {member}")
                expected_text = str(expected or "").strip().lower()
                if not re.fullmatch(r"[0-9a-f]{64}", expected_text):
                    raise ValueError(f"Backup member checksum is invalid: {member}")
                actual = _zip_member_sha256(zf, member)
                if not hmac.compare_digest(actual, expected_text):
                    raise ValueError(f"Backup member checksum mismatch: {member}")
            # Validation may need to materialize a multi-gigabyte database.
            # Keep that bounded temporary file on the persistent backup
            # filesystem instead of the container's intentionally small /tmp.
            with tempfile.TemporaryDirectory(
                prefix=".ditaknet-validate-backup-", dir=path.parent
            ) as tmp:
                extracted = Path(tmp) / "database.sqlite3"
                with (
                    zf.open("database.sqlite3") as source,
                    extracted.open("wb") as target,
                ):
                    shutil.copyfileobj(source, target)
                _validate_sqlite_database(extracted)
            return {
                "valid": True,
                "filename": path.name,
                "backup_type": manifest.get("backup_type", "full"),
                "format_version": manifest.get("format_version"),
                "app_version": manifest.get("app_version"),
                "created_at": manifest.get("created_at"),
                "includes": manifest.get("includes", []),
                "table_counts": manifest.get("table_counts", {}),
                "backup_origin": manifest.get("backup_origin"),
                "operation_context": manifest.get("operation_context") or {},
                "sha256": file_sha256(path),
            }
    _validate_sqlite_database(path)
    return {
        "valid": True,
        "filename": path.name,
        "backup_type": "database",
        "app_version": settings.app_version,
        "includes": ["database"],
        "sha256": file_sha256(path),
    }


def delete_backup(name: str) -> None:
    path = resolve_backup_path(name)
    if not path.exists():
        raise FileNotFoundError("Backup not found")
    path.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        sidecar.unlink(missing_ok=True)
