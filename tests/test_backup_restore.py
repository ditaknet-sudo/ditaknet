"""Isolated SQLite WAL backup integrity and restore compatibility tests."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Request, UploadFile

from ditaknet import database as db
from ditaknet.api import backups_ops
from ditaknet.core import backup, restore
from ditaknet.core.licensing import license_service
from ditaknet.core.process_lock import RuntimeLockError, acquire_runtime_lock
from ditaknet.security import AuthenticatedUser


def _sqlite_rows(path: Path, sql: str) -> list[tuple]:
    connection = sqlite3.connect(str(path))
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def test_full_backup_contains_committed_wal_data_and_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_database = tmp_path / "runtime" / "live.db"
    backup_directory = tmp_path / "backups"
    monkeypatch.setattr(backup.settings, "backup_dir", str(backup_directory))
    monkeypatch.setattr(
        license_service,
        "status",
        AsyncMock(return_value={"tier": "PROFESSIONAL", "valid": True}),
    )

    async def exercise() -> None:
        await db.close_db()
        try:
            connection = await db.init_db(str(live_database))
            await connection.execute(
                "INSERT INTO hosts (name, address) VALUES (?, ?)",
                ("wal-backed-host", "192.0.2.44"),
            )
            await connection.commit()
            await db.set_app_setting("site_name", "Test Lab")
            await db.set_app_setting("telegram_bot_token", "must-not-leak")

            wal_path = Path(f"{live_database}-wal")
            assert wal_path.is_file()
            assert wal_path.stat().st_size > 0

            result = await backup.create_full_backup("wal-round-trip.zip")
            validation = backup.validate_backup_file(result["filename"])
            assert validation["valid"] is True
            assert validation["format_version"] == backup.FORMAT_VERSION
            assert validation["table_counts"]["hosts"] == 1

            archive_path = Path(result["path"])
            extract_directory = tmp_path / "extracted"
            extract_directory.mkdir()
            with zipfile.ZipFile(archive_path) as archive:
                assert {
                    "database.sqlite3",
                    "backup.json",
                    "settings.json",
                    "license.json",
                    "metadata.json",
                } <= set(archive.namelist())
                manifest = json.loads(archive.read("backup.json"))
                exported_settings = json.loads(archive.read("settings.json"))
                archive.extract("database.sqlite3", extract_directory)

            assert manifest["format"] == "ditaknet-backup"
            assert manifest["table_counts"]["hosts"] == 1
            assert exported_settings["site_name"] == "Test Lab"
            assert exported_settings["telegram_bot_token"] == "[REDACTED]"

            snapshot = extract_directory / "database.sqlite3"
            assert _sqlite_rows(snapshot, "PRAGMA quick_check") == [("ok",)]
            assert _sqlite_rows(
                snapshot,
                "SELECT name, address FROM hosts WHERE name = 'wal-backed-host'",
            ) == [("wal-backed-host", "192.0.2.44")]

            database_only = backup.create_backup("wal-database.sqlite3")
            database_only_path = Path(database_only["path"])
            assert (
                backup.validate_backup_file(database_only["filename"])["valid"] is True
            )
            assert _sqlite_rows(database_only_path, "PRAGMA quick_check") == [("ok",)]
            assert _sqlite_rows(
                database_only_path,
                "SELECT name FROM hosts WHERE name = 'wal-backed-host'",
            ) == [("wal-backed-host",)]
            assert not Path(f"{database_only_path}-wal").exists()
            assert not Path(f"{database_only_path}-shm").exists()
        finally:
            await db.close_db()

    asyncio.run(exercise())


def test_validate_backup_rejects_missing_or_corrupt_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup_directory = tmp_path / "backups"
    backup_directory.mkdir()
    monkeypatch.setattr(backup.settings, "backup_dir", str(backup_directory))

    with zipfile.ZipFile(backup_directory / "missing-database.zip", "w") as archive:
        archive.writestr("backup.json", json.dumps({"format": "ditaknet-backup"}))

    with zipfile.ZipFile(backup_directory / "corrupt-database.zip", "w") as archive:
        corrupt_database = b"not a SQLite database"
        archive.writestr("database.sqlite3", corrupt_database)
        archive.writestr(
            "backup.json",
            json.dumps(
                {
                    "format": "ditaknet-backup",
                    "format_version": backup.FORMAT_VERSION,
                    "members_sha256": {
                        "database.sqlite3": hashlib.sha256(corrupt_database).hexdigest()
                    },
                }
            ),
        )

    (backup_directory / "corrupt.sqlite3").write_bytes(b"not SQLite" * 512)
    (backup_directory / "corrupt-container.zip").write_bytes(b"not a ZIP archive")

    with pytest.raises(ValueError, match="missing database.sqlite3"):
        backup.validate_backup_file("missing-database.zip")
    with pytest.raises(ValueError, match="valid SQLite"):
        backup.validate_backup_file("corrupt-database.zip")
    with pytest.raises(ValueError, match="valid SQLite"):
        backup.validate_backup_file("corrupt.sqlite3")
    with pytest.raises(ValueError, match="invalid or corrupt"):
        backup.validate_backup_file("corrupt-container.zip")


def test_zip_database_validation_uses_persistent_backup_filesystem(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup_directory = tmp_path / "backups"
    backup_directory.mkdir()
    monkeypatch.setattr(backup.settings, "backup_dir", str(backup_directory))

    source_database = tmp_path / "source.sqlite3"
    connection = sqlite3.connect(source_database)
    try:
        connection.executescript(
            "CREATE TABLE hosts (id INTEGER);"
            "CREATE TABLE services (id INTEGER);"
            "CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT);"
        )
        connection.commit()
    finally:
        connection.close()
    database_bytes = source_database.read_bytes()
    archive_path = backup_directory / "persistent-validation-temp.zip"
    manifest = {
        "format": "ditaknet-backup",
        "format_version": backup.FORMAT_VERSION,
        "members_sha256": {
            "database.sqlite3": hashlib.sha256(database_bytes).hexdigest()
        },
    }
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("database.sqlite3", database_bytes)
        archive.writestr("backup.json", json.dumps(manifest))

    real_temporary_directory = backup.tempfile.TemporaryDirectory
    validation_directories: list[Path] = []

    def tracked_temporary_directory(*args, **kwargs):
        if str(kwargs.get("prefix") or "").startswith(".ditaknet-validate-backup-"):
            validation_directories.append(Path(kwargs["dir"]).resolve())
        return real_temporary_directory(*args, **kwargs)

    monkeypatch.setattr(
        backup.tempfile, "TemporaryDirectory", tracked_temporary_directory
    )

    assert backup.validate_backup_file(archive_path.name)["valid"] is True
    assert validation_directories == [backup_directory.resolve()]
    assert list(backup_directory.glob(".ditaknet-validate-backup-*")) == []


def test_offline_restore_replaces_state_without_restamping_writer_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_database = tmp_path / "runtime" / "restore-live.db"
    backup_directory = tmp_path / "backups"
    monkeypatch.setattr(backup.settings, "backup_dir", str(backup_directory))
    monkeypatch.setattr(backup.settings, "data_dir", str(live_database.parent))
    monkeypatch.setattr(backup.settings, "database_path", str(live_database))
    monkeypatch.setattr(license_service, "status", AsyncMock(return_value={}))

    async def exercise() -> None:
        await db.close_db()
        try:
            connection = await db.init_db(str(live_database))
            await connection.execute(
                "INSERT INTO hosts (name, address) VALUES (?, ?)",
                ("present-in-backup", "198.51.100.10"),
            )
            await connection.commit()
            await db.set_app_setting("restore_marker", "before")
            await db.set_app_setting("database_last_writer_version", "2.0.0")
            created = await backup.create_full_backup("restore-point.zip")

            await connection.execute(
                "INSERT INTO hosts (name, address) VALUES (?, ?)",
                ("created-after-backup", "198.51.100.20"),
            )
            await connection.commit()
            await db.set_app_setting("restore_marker", "after")
            await db.set_app_setting("database_last_writer_version", "2.0.1")
            await db.close_db()

            result = restore.restore_backup_offline(
                created["filename"],
                confirmation="RESTORE restore-point.zip",
                expected_sha256=created["sha256"],
            )

            assert result["ok"] is True
            assert result["restored_from"] == "restore-point.zip"
            assert (backup_directory / result["pre_restore_backup"]).is_file()
            assert (backup_directory / result["receipt"]).is_file()
            assert result["database_reopened_by_restore"] is False

            host_rows = _sqlite_rows(
                live_database, "SELECT name FROM hosts ORDER BY name"
            )
            assert [str(row[0]) for row in host_rows] == ["present-in-backup"]
            assert _sqlite_rows(
                live_database,
                "SELECT value FROM app_settings WHERE key = 'restore_marker'",
            ) == [("before",)]
            assert _sqlite_rows(
                live_database,
                "SELECT value FROM app_settings WHERE key = 'database_last_writer_version'",
            ) == [("2.0.0",)]
            assert _sqlite_rows(live_database, "PRAGMA quick_check") == [("ok",)]
        finally:
            await db.close_db()

    asyncio.run(exercise())


def test_failed_offline_replace_restores_current_database_and_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_database = tmp_path / "runtime" / "rollback-live.db"
    backup_directory = tmp_path / "backups"
    monkeypatch.setattr(backup.settings, "backup_dir", str(backup_directory))
    monkeypatch.setattr(backup.settings, "data_dir", str(live_database.parent))
    monkeypatch.setattr(backup.settings, "database_path", str(live_database))
    monkeypatch.setattr(license_service, "status", AsyncMock(return_value={}))

    async def exercise() -> None:
        await db.close_db()
        try:
            connection = await db.init_db(str(live_database))
            await connection.execute(
                "INSERT INTO hosts (name, address) VALUES (?, ?)",
                ("backup-host", "203.0.113.10"),
            )
            await connection.commit()
            created = await backup.create_full_backup("rollback-source.zip")

            await connection.execute(
                "INSERT INTO hosts (name, address) VALUES (?, ?)",
                ("current-wal-host", "203.0.113.20"),
            )
            await connection.commit()
            assert Path(f"{live_database}-wal").stat().st_size > 0
            await db.close_db()

            original_replace = restore.os.replace

            def fail_staged_replace(source, destination) -> None:
                if Path(source).name == "restored.sqlite3":
                    assert Path(destination) == live_database
                    assert live_database.is_file()
                    raise OSError("synthetic atomic replace failure")
                original_replace(source, destination)

            monkeypatch.setattr(restore.os, "replace", fail_staged_replace)
            with pytest.raises(OSError, match="synthetic atomic replace failure"):
                restore.restore_backup_offline(
                    created["filename"],
                    confirmation="RESTORE rollback-source.zip",
                    expected_sha256=created["sha256"],
                )

            rows = _sqlite_rows(live_database, "SELECT name FROM hosts ORDER BY name")
            assert [str(row[0]) for row in rows] == ["backup-host", "current-wal-host"]
            assert _sqlite_rows(live_database, "PRAGMA quick_check") == [("ok",)]
        finally:
            await db.close_db()

    asyncio.run(exercise())


def test_restore_requires_explicit_confirmation() -> None:
    with pytest.raises(ValueError, match="confirm=true"):
        asyncio.run(restore.restore_from_backup("unused.zip", confirm=False))


def test_backup_v2_requires_complete_checksums_and_unique_members(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup_directory = tmp_path / "backups"
    backup_directory.mkdir()
    monkeypatch.setattr(backup.settings, "backup_dir", str(backup_directory))

    incomplete = backup_directory / "incomplete-v2.zip"
    with zipfile.ZipFile(incomplete, "w") as archive:
        archive.writestr("database.sqlite3", b"not-read-before-checksum-gate")
        archive.writestr(
            "backup.json",
            json.dumps(
                {
                    "format": "ditaknet-backup",
                    "format_version": 2,
                    "members_sha256": {},
                }
            ),
        )
    with pytest.raises(ValueError, match="checksums for every data member"):
        backup.validate_backup_file(incomplete.name)

    duplicate = backup_directory / "duplicate-members.zip"
    with pytest.warns(UserWarning, match="Duplicate name"):
        with zipfile.ZipFile(duplicate, "w") as archive:
            archive.writestr("database.sqlite3", b"first")
            archive.writestr("database.sqlite3", b"second")
    with pytest.raises(ValueError, match="duplicate member names"):
        backup.validate_backup_file(duplicate.name)


def test_same_name_backup_refuses_overwrite_and_preserves_recovery_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_database = tmp_path / "runtime" / "live.db"
    backup_directory = tmp_path / "backups"
    monkeypatch.setattr(backup.settings, "backup_dir", str(backup_directory))

    async def exercise() -> None:
        await db.close_db()
        try:
            await db.init_db(str(live_database))
            created = await backup.create_full_backup("do-not-overwrite.zip")
            path = Path(created["path"])
            before = path.read_bytes()

            with pytest.raises(FileExistsError, match="already exists"):
                await backup.create_full_backup("do-not-overwrite.zip")

            assert path.read_bytes() == before
            assert backup.validate_backup_file(path.name)["valid"] is True
        finally:
            await db.close_db()

    asyncio.run(exercise())


def test_invalid_same_name_upload_cannot_overwrite_valid_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_database = tmp_path / "runtime" / "live.db"
    backup_directory = tmp_path / "backups"
    monkeypatch.setattr(backup.settings, "backup_dir", str(backup_directory))

    async def exercise() -> None:
        await db.close_db()
        try:
            await db.init_db(str(live_database))
            created = await backup.create_full_backup("existing-recovery-point.zip")
            existing_path = Path(created["path"])
            original_bytes = existing_path.read_bytes()
            upload = UploadFile(
                filename=existing_path.name,
                file=BytesIO(b"this is not a valid DitakNet backup"),
            )
            request = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/api/backups/upload",
                    "headers": [],
                    "client": ("127.0.0.1", 12345),
                }
            )

            try:
                with pytest.raises(HTTPException) as caught:
                    await backups_ops.upload_backup(
                        request=request,
                        file=upload,
                        user=AuthenticatedUser(username="pytest", is_superadmin=True),
                        _operation_guard=None,
                    )
                assert caught.value.status_code == 409
            finally:
                await upload.close()

            assert existing_path.read_bytes() == original_bytes
            assert backup.validate_backup_file(existing_path.name)["valid"] is True
            assert list(backup_directory.glob(".ditaknet-upload-*")) == []
        finally:
            await db.close_db()

    asyncio.run(exercise())


def test_oversized_upload_is_rejected_without_publishing_partial_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup_directory = tmp_path / "backups"
    monkeypatch.setattr(backup.settings, "backup_dir", str(backup_directory))
    monkeypatch.setattr(backups_ops, "MAX_BACKUP_FILE_BYTES", 8)

    async def exercise() -> None:
        upload = UploadFile(
            filename="oversized.zip",
            file=BytesIO(b"more-than-eight-bytes"),
        )
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/backups/upload",
                "headers": [],
                "client": ("127.0.0.1", 12345),
            }
        )
        try:
            with pytest.raises(HTTPException) as caught:
                await backups_ops.upload_backup(
                    request=request,
                    file=upload,
                    user=AuthenticatedUser(username="pytest", is_superadmin=True),
                    _operation_guard=None,
                )
            assert caught.value.status_code == 413
        finally:
            await upload.close()

        assert not (backup_directory / "oversized.zip").exists()
        assert list(backup_directory.glob(".ditaknet-upload-*")) == []

    asyncio.run(exercise())


def test_live_restore_is_rejected_and_runtime_lock_blocks_offline_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_database = tmp_path / "runtime" / "locked-live.db"
    backup_directory = tmp_path / "backups"
    monkeypatch.setattr(backup.settings, "backup_dir", str(backup_directory))
    monkeypatch.setattr(backup.settings, "data_dir", str(live_database.parent))
    monkeypatch.setattr(backup.settings, "database_path", str(live_database))
    monkeypatch.setattr(license_service, "status", AsyncMock(return_value={}))

    async def exercise() -> None:
        await db.close_db()
        connection = await db.init_db(str(live_database))
        await connection.execute(
            "INSERT INTO hosts (name, address) VALUES (?, ?)",
            ("must-survive-live-request", "192.0.2.99"),
        )
        await connection.commit()
        created = await backup.create_full_backup("locked-restore.zip")

        with pytest.raises(
            restore.OfflineRestoreRequired, match="Live restore is disabled"
        ):
            await restore.restore_from_backup(
                created["filename"],
                confirm=True,
                actor="pytest",
            )
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": f"/api/backups/{created['filename']}/restore",
                "headers": [],
                "client": ("127.0.0.1", 12345),
            }
        )
        with pytest.raises(HTTPException) as caught:
            await backups_ops.restore_backup(
                filename=created["filename"],
                body=backups_ops.RestoreRequest(confirm=True),
                request=request,
                user=AuthenticatedUser(username="pytest", is_superadmin=True),
            )
        assert caught.value.status_code == 409
        assert caught.value.headers == {"X-DitakNet-Restore-Mode": "offline-required"}
        rows = await connection.execute_fetchall(
            "SELECT name FROM hosts WHERE name = 'must-survive-live-request'"
        )
        assert [str(row[0]) for row in rows] == ["must-survive-live-request"]
        await db.close_db()

        with acquire_runtime_lock(live_database.parent):
            with pytest.raises(RuntimeLockError, match="still running"):
                restore.restore_backup_offline(
                    created["filename"],
                    confirmation="RESTORE locked-restore.zip",
                    expected_sha256=created["sha256"],
                )

    try:
        asyncio.run(exercise())
    finally:
        asyncio.run(db.close_db())


@pytest.mark.parametrize(
    ("member_name", "limit_name"),
    [
        ("backup.json", "MAX_BACKUP_MANIFEST_BYTES"),
        ("settings.json", "MAX_BACKUP_SETTINGS_BYTES"),
        ("license.json", "MAX_BACKUP_LICENSE_BYTES"),
        ("metadata.json", "MAX_BACKUP_METADATA_BYTES"),
        ("logs/ditaknet.log", "MAX_BACKUP_LOG_MEMBER_BYTES"),
        ("future-data.bin", "MAX_BACKUP_OTHER_MEMBER_BYTES"),
    ],
)
def test_backup_zip_applies_member_specific_size_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    member_name: str,
    limit_name: str,
) -> None:
    backup_directory = tmp_path / "backups"
    backup_directory.mkdir()
    monkeypatch.setattr(backup.settings, "backup_dir", str(backup_directory))
    monkeypatch.setattr(backup, limit_name, 8)

    archive_path = backup_directory / f"oversized-{Path(member_name).name}.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("database.sqlite3", b"x")
        archive.writestr(member_name, b"nine-byte")

    with pytest.raises(ValueError, match=f"too large: {member_name}"):
        backup.validate_backup_file(archive_path.name)


def test_uploaded_backup_validation_runs_outside_the_async_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup_directory = tmp_path / "backups"
    monkeypatch.setattr(backup.settings, "backup_dir", str(backup_directory))
    monkeypatch.setattr(db, "create_audit_log", AsyncMock())
    validation_threads: list[int] = []

    def validate_in_worker(filename: str) -> dict:
        validation_threads.append(threading.get_ident())
        return {"valid": True, "filename": filename, "sha256": "a" * 64}

    monkeypatch.setattr(backups_ops, "validate_backup_file", validate_in_worker)

    async def exercise() -> None:
        event_loop_thread = threading.get_ident()
        upload = UploadFile(
            filename="worker-validated.zip",
            file=BytesIO(b"synthetic archive payload"),
        )
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/backups/upload",
                "headers": [],
                "client": ("127.0.0.1", 12345),
            }
        )
        try:
            result = await backups_ops.upload_backup(
                request=request,
                file=upload,
                user=AuthenticatedUser(username="pytest", is_superadmin=True),
                _operation_guard=None,
            )
        finally:
            await upload.close()

        assert result["filename"] == "worker-validated.zip"
        assert validation_threads
        assert all(thread_id != event_loop_thread for thread_id in validation_threads)
        assert (backup_directory / result["filename"]).read_bytes() == (
            b"synthetic archive payload"
        )
        explicit_validation = await backups_ops.validate_backup(
            filename=result["filename"],
            user=AuthenticatedUser(username="pytest", is_superadmin=True),
        )
        assert explicit_validation["valid"] is True
        assert len(validation_threads) == 2
        assert list(backup_directory.glob(".ditaknet-upload-*")) == []

    asyncio.run(exercise())


def test_oversized_uploaded_manifest_is_not_published(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backup_directory = tmp_path / "backups"
    monkeypatch.setattr(backup.settings, "backup_dir", str(backup_directory))
    monkeypatch.setattr(backup, "MAX_BACKUP_MANIFEST_BYTES", 64)

    payload = BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("database.sqlite3", b"x")
        archive.writestr("backup.json", b"{" + (b" " * 64) + b"}")
    payload.seek(0)

    async def exercise() -> None:
        upload = UploadFile(filename="oversized-manifest.zip", file=payload)
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/backups/upload",
                "headers": [],
                "client": ("127.0.0.1", 12345),
            }
        )
        try:
            with pytest.raises(HTTPException) as caught:
                await backups_ops.upload_backup(
                    request=request,
                    file=upload,
                    user=AuthenticatedUser(username="pytest", is_superadmin=True),
                    _operation_guard=None,
                )
            assert caught.value.status_code == 400
            assert "backup.json" in str(caught.value.detail)
        finally:
            await upload.close()

        assert not (backup_directory / "oversized-manifest.zip").exists()
        assert list(backup_directory.glob(".ditaknet-upload-*")) == []

    asyncio.run(exercise())
