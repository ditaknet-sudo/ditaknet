"""Isolated SQLite initialization and schema migration regression tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

from ditaknet import database as db
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
            assert {"permissions_json", "session_version", "locked_until"} <= user_columns
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

            assert [tuple(row) for row in hosts] == [
                ("persistent-host", "192.0.2.10")
            ]
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
