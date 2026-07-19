"""Isolated SQLite WAL backup integrity and restore compatibility tests."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from ditaknet import database as db
from ditaknet.core import backup, restore
from ditaknet.core.licensing import license_service


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
            assert backup.validate_backup_file(database_only["filename"])["valid"] is True
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
        archive.writestr("database.sqlite3", b"not a SQLite database")
        archive.writestr(
            "backup.json",
            json.dumps(
                {"format": "ditaknet-backup", "format_version": backup.FORMAT_VERSION}
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


def test_restore_round_trip_replaces_newer_data_with_valid_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_database = tmp_path / "runtime" / "restore-live.db"
    backup_directory = tmp_path / "backups"
    monkeypatch.setattr(backup.settings, "backup_dir", str(backup_directory))
    monkeypatch.setattr(license_service, "status", AsyncMock(return_value={}))
    monkeypatch.setattr(license_service, "ensure_default_license", AsyncMock())

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
            created = await backup.create_full_backup("restore-point.zip")

            await connection.execute(
                "INSERT INTO hosts (name, address) VALUES (?, ?)",
                ("created-after-backup", "198.51.100.20"),
            )
            await connection.commit()
            await db.set_app_setting("restore_marker", "after")

            result = await restore.restore_from_backup(
                created["filename"],
                mode="full_restore",
                confirm=True,
                actor="pytest",
            )

            assert result["ok"] is True
            assert result["validation"]["valid"] is True
            assert result["restored_from"] == "restore-point.zip"
            assert (backup_directory / result["pre_restore_backup"]).is_file()

            restored = await db.get_db()
            host_rows = await restored.execute_fetchall("SELECT name FROM hosts ORDER BY name")
            assert [str(row[0]) for row in host_rows] == ["present-in-backup"]
            assert await db.get_app_setting("restore_marker") == "before"
            assert (await restored.execute_fetchall("PRAGMA quick_check"))[0][0] == "ok"
            assert (await db.schema_health())["ok"] is True
        finally:
            await db.close_db()

    asyncio.run(exercise())


def test_failed_restore_rolls_back_committed_wal_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_database = tmp_path / "runtime" / "rollback-live.db"
    backup_directory = tmp_path / "backups"
    monkeypatch.setattr(backup.settings, "backup_dir", str(backup_directory))
    monkeypatch.setattr(license_service, "status", AsyncMock(return_value={}))
    monkeypatch.setattr(
        license_service,
        "ensure_default_license",
        AsyncMock(side_effect=RuntimeError("synthetic post-restore failure")),
    )

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

            with pytest.raises(RuntimeError, match="synthetic post-restore failure"):
                await restore.restore_from_backup(
                    created["filename"],
                    mode="full_restore",
                    confirm=True,
                    actor="pytest",
                )

            rolled_back = await db.get_db()
            rows = await rolled_back.execute_fetchall("SELECT name FROM hosts ORDER BY name")
            assert [str(row[0]) for row in rows] == ["backup-host", "current-wal-host"]
            assert (await rolled_back.execute_fetchall("PRAGMA quick_check"))[0][0] == "ok"
        finally:
            await db.close_db()

    asyncio.run(exercise())


def test_restore_requires_explicit_confirmation() -> None:
    with pytest.raises(ValueError, match="confirm=true"):
        asyncio.run(restore.restore_from_backup("unused.zip", confirm=False))
