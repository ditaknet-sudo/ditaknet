"""Offline-only backup restore primitives.

Replacing SQLite while the web process is alive cannot safely drain every
request, scheduler job, and plugin writer. DitakNet therefore validates and
performs destructive restore only while holding the process-lifetime runtime
lock that the web service owns. The restored database is deliberately not
opened by the restoring image, so its original schema/last-writer markers stay
intact for a compatibility-aware rollback to an older image.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shlex
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ditaknet.core.backup import (
    MAX_ZIP_MEMBER_BYTES,
    _create_sqlite_snapshot,
    _validate_sqlite_database,
    backup_root,
    create_backup,
    database_path,
    file_sha256,
    resolve_backup_path,
    validate_backup_file,
)
from ditaknet.core.process_lock import acquire_runtime_lock


_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class OfflineRestoreRequired(RuntimeError):
    """Raised when a caller attempts an in-process destructive restore."""


def offline_restore_command(filename: str, expected_sha256: str) -> str:
    """Return the one-shot command to run after the DitakNet service is stopped."""

    safe_name = Path(filename).name
    confirmation = f"RESTORE {safe_name}"
    return " ".join(
        [
            "docker compose run --rm --no-deps --entrypoint python ditaknet",
            "-m ditaknet.offline_restore",
            "--backup",
            shlex.quote(safe_name),
            "--expected-sha256",
            shlex.quote(expected_sha256),
            "--confirm",
            shlex.quote(confirmation),
        ]
    )


def _copy_member_bounded(
    archive: zipfile.ZipFile,
    member: str,
    destination: Path,
) -> None:
    written = 0
    with archive.open(member, "r") as source, destination.open("xb") as output:
        while chunk := source.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_ZIP_MEMBER_BYTES:
                raise ValueError("Backup database exceeds the supported size limit")
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())


def _stage_database(backup_path: Path, destination: Path) -> None:
    if backup_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(backup_path) as archive:
            _copy_member_bounded(archive, "database.sqlite3", destination)
    else:
        _create_sqlite_snapshot(backup_path, destination)
    _validate_sqlite_database(destination)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_file(path: Path) -> None:
    with path.open("rb+") as handle:
        os.fsync(handle.fileno())


def _checkpoint_live_database(path: Path) -> None:
    """Make the stopped live database self-contained before atomic replacement."""

    connection = sqlite3.connect(str(path), timeout=5)
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if result and int(result[0]) != 0:
            raise RuntimeError(
                "Could not checkpoint the live database; another writer may still be running"
            )
    finally:
        connection.close()

    # After a successful TRUNCATE checkpoint, the main DB contains every
    # committed page. Removing empty/stale sidecars before the final replace
    # makes both the old and restored target crash-consistent.
    for suffix in ("-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    _validate_sqlite_database(path)
    _fsync_file(path)
    _fsync_directory(path.parent)


def _write_receipt(value: dict[str, Any]) -> Path:
    root = backup_root()
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    target = root / f"offline-restore-receipt-{timestamp}.json"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            prefix=".offline-restore-receipt-",
            suffix=".tmp",
            dir=root,
            delete=False,
        ) as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, target)
        _fsync_directory(root)
        return target
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def restore_backup_offline(
    filename: str,
    *,
    confirmation: str,
    expected_sha256: str,
) -> dict[str, Any]:
    """Restore after explicit shutdown and exclusive runtime-lock acquisition.

    Images predating the runtime lock cannot advertise that they are alive, so
    operators must still stop every legacy container before invoking this tool.
    """

    backup_path = resolve_backup_path(filename)
    expected_confirmation = f"RESTORE {backup_path.name}"
    if not secrets.compare_digest(str(confirmation or ""), expected_confirmation):
        raise ValueError(f"Confirmation must exactly match {expected_confirmation}")

    expected_hash = str(expected_sha256 or "").strip().lower()
    if _SHA256_RE.fullmatch(expected_hash) is None:
        raise ValueError("expected_sha256 must be exactly 64 lowercase hex characters")

    target_db = database_path()
    with acquire_runtime_lock(target_db.parent):
        validation = validate_backup_file(backup_path.name)
        actual_hash = str(validation.get("sha256") or "").lower()
        if not secrets.compare_digest(actual_hash, expected_hash):
            raise ValueError(
                "Backup SHA-256 does not match the approved recovery point"
            )

        target_db.parent.mkdir(parents=True, exist_ok=True)
        previous_backup: dict[str, Any] | None = None
        if target_db.is_file():
            snapshot_name = (
                "ditaknet-pre-offline-restore-"
                f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S-%f')}.sqlite3"
            )
            previous_backup = create_backup(snapshot_name)
            previous_validation = validate_backup_file(previous_backup["filename"])
            previous_backup["sha256"] = previous_validation["sha256"]
            previous_path = resolve_backup_path(previous_backup["filename"])
            _fsync_file(previous_path)
            _fsync_directory(previous_path.parent)
            _checkpoint_live_database(target_db)

        restored_database_hash = ""
        with tempfile.TemporaryDirectory(
            prefix=".ditaknet-offline-restore-", dir=target_db.parent
        ) as temporary_name:
            temporary_root = Path(temporary_name)
            staged_db = temporary_root / "restored.sqlite3"
            _stage_database(backup_path, staged_db)
            restored_database_hash = file_sha256(staged_db)
            _fsync_file(staged_db)

            # One crash-atomic namespace operation: before it, the checkpointed
            # old DB is authoritative; after it, the self-contained staged DB is.
            for suffix in ("-wal", "-shm"):
                Path(f"{target_db}{suffix}").unlink(missing_ok=True)
            os.replace(staged_db, target_db)
            _fsync_directory(target_db.parent)
            _validate_sqlite_database(target_db)

        completed_at = datetime.now(UTC).isoformat()
        receipt_value = {
            "operation": "offline_restore",
            "completed_at": completed_at,
            "restored_from": backup_path.name,
            "backup_sha256": actual_hash,
            "restored_database_sha256": restored_database_hash,
            "backup_format_version": validation.get("format_version"),
            "backup_app_version": validation.get("app_version"),
            "pre_restore_backup": (
                previous_backup["filename"] if previous_backup is not None else None
            ),
            "pre_restore_backup_sha256": (
                previous_backup.get("sha256") if previous_backup is not None else None
            ),
            "database_reopened_by_restore": False,
        }
        receipt = _write_receipt(receipt_value)
        return {
            "ok": True,
            **receipt_value,
            "receipt": receipt.name,
        }


async def restore_from_backup(
    filename: str,
    *,
    confirm: bool = False,
    actor: str = "admin",
) -> dict[str, Any]:
    """Reject unsafe in-process restore and direct operators to offline recovery."""

    del actor
    if not confirm:
        raise ValueError("Restore requires confirm=true")
    validation = await asyncio.to_thread(validate_backup_file, filename)
    raise OfflineRestoreRequired(
        "Live restore is disabled. Stop every DitakNet container and run: "
        + offline_restore_command(filename, str(validation["sha256"]))
    )
