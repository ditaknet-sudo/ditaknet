"""Isolated SQLite initialization and schema migration regression tests."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from ditaknet import database as db
from ditaknet.config import settings
from ditaknet.core import backup
from ditaknet.core.rbac import DEFAULT_ROLES


def test_init_db_creates_healthy_schema_in_temporary_path(tmp_path: Path) -> None:
    database_path = tmp_path / "isolated" / "ditaknet-test.db"

    async def exercise() -> None:
        await db.close_db()
        try:
            connection = await db.init_db(str(database_path))

            assert db.get_db_path() == database_path.resolve()
            assert database_path.is_file()

            journal_mode = await connection.execute_fetchall("PRAGMA journal_mode")
            foreign_keys = await connection.execute_fetchall("PRAGMA foreign_keys")
            assert str(journal_mode[0][0]).lower() == "wal"
            assert int(foreign_keys[0][0]) == 1

            health = await db.schema_health()
            assert health["ok"] is True
            assert health["missing_tables"] == []
            assert health["missing_columns"] == {}
            assert health["missing_migrations"] == []
            assert health["schema_revision"] == db.DATABASE_SCHEMA_REVISION
            assert health["quick_check"] == ["ok"]
            assert health["foreign_key_violations"] == 0

            table_rows = await connection.execute_fetchall(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
            tables = {str(row[0]) for row in table_rows}
            assert set(db.CORE_TABLES) <= tables
            assert {"discovery_inventory", "discovery_change_events"} <= tables

            host_columns = {
                str(row[1])
                for row in await connection.execute_fetchall("PRAGMA table_info(hosts)")
            }
            user_columns = {
                str(row[1])
                for row in await connection.execute_fetchall("PRAGMA table_info(users)")
            }
            assert {"host_type", "location", "parent_device_id"} <= host_columns
            assert {
                "permissions_json",
                "session_version",
                "locked_until",
            } <= user_columns
        finally:
            await db.close_db()

    asyncio.run(exercise())


def test_version_change_creates_validated_pre_migration_recovery_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "upgrade.db"
    backup_directory = tmp_path / "backups"
    monkeypatch.setattr(backup.settings, "backup_dir", str(backup_directory))

    async def exercise() -> None:
        await db.close_db()
        try:
            connection = await db.init_db(str(database_path))
            await connection.execute(
                "UPDATE app_settings SET value = ? WHERE key = ?",
                ("2.0.0", "database_last_writer_version"),
            )
            await connection.commit()
            await db.close_db()

            await db.init_db(str(database_path))
            recovery_points = sorted(
                backup_directory.glob("ditaknet-pre-migration-*.zip")
            )
            assert len(recovery_points) == 1
            validation = backup.validate_backup_file(recovery_points[0].name)
            assert validation["valid"] is True
            assert validation["backup_origin"] == "pre_migration"
            assert validation["operation_context"]["source_version"] == "2.0.0"
            assert (
                validation["operation_context"]["target_version"]
                == settings.app_version
            )
        finally:
            await db.close_db()

    asyncio.run(exercise())


def test_future_schema_is_rejected_before_database_mutation(tmp_path: Path) -> None:
    database_path = tmp_path / "future-schema.db"

    async def exercise() -> None:
        await db.close_db()
        connection = await db.init_db(str(database_path))
        await connection.execute(
            "UPDATE app_settings SET value = ? WHERE key = ?",
            (str(db.DATABASE_SCHEMA_REVISION + 1), "database_schema_revision"),
        )
        await connection.commit()
        await db.close_db()

        before_hash = hashlib.sha256(database_path.read_bytes()).hexdigest()
        before_mtime = database_path.stat().st_mtime_ns
        with pytest.raises(RuntimeError, match="newer DitakNet version"):
            await db.init_db(str(database_path))
        assert hashlib.sha256(database_path.read_bytes()).hexdigest() == before_hash
        assert database_path.stat().st_mtime_ns == before_mtime
        assert db._db is None

    asyncio.run(exercise())


def test_application_downgrade_requires_state_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "downgrade.db"

    async def exercise() -> None:
        await db.close_db()
        await db.init_db(str(database_path))
        await db.close_db()
        monkeypatch.setattr(db.settings, "app_version", "2.0.0")

        with pytest.raises(RuntimeError, match="downgrade detected"):
            await db.init_db(str(database_path))
        assert db._db is None

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("writer_version", "older_version"),
    [
        ("2.0.1", "2.0.1-rc.1"),
        ("2.0.1-beta.2", "2.0.1-beta.1"),
    ],
)
def test_prerelease_downgrade_is_rejected_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    writer_version: str,
    older_version: str,
) -> None:
    database_path = tmp_path / f"prerelease-{older_version}.db"

    async def exercise() -> None:
        await db.close_db()
        monkeypatch.setattr(db.settings, "app_version", writer_version)
        await db.init_db(str(database_path))
        await db.close_db()
        before_hash = hashlib.sha256(database_path.read_bytes()).hexdigest()

        monkeypatch.setattr(db.settings, "app_version", older_version)
        with pytest.raises(RuntimeError, match="downgrade detected"):
            await db.init_db(str(database_path))
        assert hashlib.sha256(database_path.read_bytes()).hexdigest() == before_hash
        assert db._db is None

    asyncio.run(exercise())


def test_migration_fingerprint_change_creates_recovery_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "fingerprint-change.db"
    backup_directory = tmp_path / "backups"
    monkeypatch.setattr(backup.settings, "backup_dir", str(backup_directory))

    async def exercise() -> None:
        await db.close_db()
        try:
            connection = await db.init_db(str(database_path))
            await connection.execute(
                "UPDATE app_settings SET value = ? WHERE key = ?",
                ("0" * 64, "database_migration_fingerprint"),
            )
            await connection.commit()
            await db.close_db()

            await db.init_db(str(database_path))
            recovery_points = list(
                backup_directory.glob("ditaknet-pre-migration-*.zip")
            )
            assert len(recovery_points) == 1
            validation = backup.validate_backup_file(recovery_points[0].name)
            assert validation["backup_origin"] == "pre_migration"
        finally:
            await db.close_db()

    asyncio.run(exercise())


def test_init_db_is_idempotent_and_preserves_existing_data(tmp_path: Path) -> None:
    database_path = tmp_path / "restart-test.db"

    async def exercise() -> None:
        await db.close_db()
        try:
            first = await db.init_db(str(database_path))
            await first.execute(
                "INSERT INTO hosts (name, address) VALUES (?, ?)",
                ("persistent-host", "192.0.2.10"),
            )
            await first.commit()

            migration_count = await first.execute_fetchall(
                "SELECT COUNT(*) FROM schema_migrations"
            )
            role_count = await first.execute_fetchall("SELECT COUNT(*) FROM roles")
            assert int(migration_count[0][0]) == len(db.MIGRATION_SQL)
            assert int(role_count[0][0]) == len(DEFAULT_ROLES)

            await db.close_db()
            second = await db.init_db(str(database_path))

            hosts = await second.execute_fetchall(
                "SELECT name, address FROM hosts WHERE name = ?",
                ("persistent-host",),
            )
            migrations_after_restart = await second.execute_fetchall(
                "SELECT id FROM schema_migrations ORDER BY id"
            )
            roles_after_restart = await second.execute_fetchall(
                "SELECT code FROM roles ORDER BY code"
            )

            assert [tuple(row) for row in hosts] == [("persistent-host", "192.0.2.10")]
            assert len(migrations_after_restart) == len(db.MIGRATION_SQL)
            assert len({str(row[0]) for row in migrations_after_restart}) == len(
                db.MIGRATION_SQL
            )
            assert len(roles_after_restart) == len(DEFAULT_ROLES)
            assert (await db.schema_health())["ok"] is True
        finally:
            await db.close_db()

    asyncio.run(exercise())


def test_legacy_schema_column_is_applied_through_real_alter_path(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy-upgrade.db"
    migration_sql = next(
        sql for sql in db.MIGRATION_SQL if "hosts ADD COLUMN location" in sql
    )
    migration_id = db._migration_id(migration_sql)

    async def exercise() -> None:
        await db.close_db()
        try:
            connection = await db.init_db(str(database_path))
            await connection.execute("ALTER TABLE hosts DROP COLUMN location")
            await connection.execute(
                "DELETE FROM schema_migrations WHERE id = ?",
                (migration_id,),
            )
            await connection.commit()

            before = {
                str(row[1])
                for row in await connection.execute_fetchall("PRAGMA table_info(hosts)")
            }
            assert "location" not in before

            await db._run_migrations(connection)
            await connection.commit()

            after = {
                str(row[1])
                for row in await connection.execute_fetchall("PRAGMA table_info(hosts)")
            }
            ledger = await connection.execute_fetchall(
                "SELECT id FROM schema_migrations WHERE id = ?",
                (migration_id,),
            )
            assert "location" in after
            assert len(ledger) == 1
        finally:
            await db.close_db()

    asyncio.run(exercise())
