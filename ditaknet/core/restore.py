"""Safe backup restore with pre-restore snapshot and admin reset modes."""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Literal

from loguru import logger

from ditaknet import database as db
from ditaknet.core.backup import (
    _create_sqlite_snapshot,
    backup_root,
    create_full_backup,
    database_path,
    resolve_backup_path,
    validate_backup_file,
)
from ditaknet.core.licensing import license_service
from ditaknet.core.setup_state import complete_setup, save_admin_credentials
from ditaknet.security import hash_password

RestoreMode = Literal["full_restore", "full_restore_reset_admin", "data_only"]


def _sqlite_sidecars(db_path: Path) -> list[Path]:
    paths = [db_path]
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{db_path}{suffix}")
        if sidecar.is_file():
            paths.append(sidecar)
    return paths


async def _save_admin_snapshot() -> dict[str, str]:
    username = await db.get_app_setting("admin_username") or ""
    password_hash = await db.get_app_setting("admin_password_hash") or ""
    return {"admin_username": username, "admin_password_hash": password_hash}


async def _apply_admin_snapshot(snapshot: dict[str, str]) -> None:
    if snapshot.get("admin_username"):
        await db.set_app_setting("admin_username", snapshot["admin_username"])
    if snapshot.get("admin_password_hash"):
        await db.set_app_setting("admin_password_hash", snapshot["admin_password_hash"])


async def _save_auth_snapshot() -> dict[str, Any]:
    conn = await db.get_db()
    users = await conn.execute_fetchall("SELECT * FROM users ORDER BY id")
    roles = await conn.execute_fetchall("SELECT * FROM roles ORDER BY code")
    return {
        "admin": await _save_admin_snapshot(),
        "users": [dict(row) for row in users],
        "roles": [dict(row) for row in roles],
    }


async def _apply_auth_snapshot(snapshot: dict[str, Any]) -> None:
    await _apply_admin_snapshot(snapshot.get("admin") or {})
    conn = await db.get_db()
    await conn.execute("DELETE FROM users")
    await conn.execute("DELETE FROM roles")
    for role in snapshot.get("roles") or []:
        await conn.execute(
            """INSERT INTO roles
               (code, name, description, permissions_json, is_system, license_feature, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                role.get("code"),
                role.get("name"),
                role.get("description", ""),
                role.get("permissions_json", "[]"),
                int(role.get("is_system", 1)),
                role.get("license_feature", ""),
                role.get("created_at"),
                role.get("updated_at"),
            ),
        )
    for row in snapshot.get("users") or []:
        await conn.execute(
            """INSERT INTO users
               (id, username, full_name, email, phone, telegram, role, is_active, is_superadmin,
                created_at, updated_at, last_login_at, password_hash, must_change_password,
                failed_login_count, locked_until, permissions_json, session_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row.get("id"),
                row.get("username"),
                row.get("full_name", ""),
                row.get("email", ""),
                row.get("phone", ""),
                row.get("telegram", ""),
                row.get("role", "viewer"),
                int(row.get("is_active", 1)),
                int(row.get("is_superadmin", 0)),
                row.get("created_at"),
                row.get("updated_at"),
                row.get("last_login_at"),
                row.get("password_hash"),
                int(row.get("must_change_password", 0)),
                int(row.get("failed_login_count", 0)),
                row.get("locked_until"),
                row.get("permissions_json", "[]"),
                int(row.get("session_version", 0)),
            ),
        )
    await conn.commit()


async def restore_from_backup(
    filename: str,
    *,
    mode: RestoreMode = "full_restore",
    confirm: bool = False,
    new_admin_username: str | None = None,
    new_admin_password: str | None = None,
    actor: str = "admin",
) -> dict[str, Any]:
    if not confirm:
        raise ValueError("Restore requires confirm=true")

    validation = validate_backup_file(filename)
    backup_path = resolve_backup_path(filename)
    pre_restore = await create_full_backup(name=None)
    auth_snapshot = await _save_auth_snapshot() if mode == "data_only" else {}

    scheduler = None
    try:
        from ditaknet.api.deps import get_scheduler

        scheduler = get_scheduler()
        if getattr(scheduler, "_scheduler", None):
            scheduler._scheduler.pause()
    except Exception as exc:
        logger.warning("Could not pause scheduler before restore: {}", exc)

    target_db = database_path()
    rollback_db: Path | None = None

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            if backup_path.suffix.lower() == ".zip":
                with zipfile.ZipFile(backup_path) as zf:
                    zf.extract("database.sqlite3", tmp_path)
                source_db = tmp_path / "database.sqlite3"
            else:
                # Legacy database-only backups may have WAL/SHM sidecars.
                # Merge them into one self-contained snapshot before restore.
                source_db = tmp_path / "database.sqlite3"
                _create_sqlite_snapshot(backup_path, source_db)

            if not source_db.exists():
                raise FileNotFoundError("Backup database missing")

            await db.close_db()
            rollback_db = target_db.with_suffix(target_db.suffix + ".rollback")
            if target_db.exists():
                shutil.copy2(target_db, rollback_db)

            for path in _sqlite_sidecars(target_db):
                path.unlink(missing_ok=True)
            target_db.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_db, target_db)

        await db.init_db(str(target_db))
        await license_service.ensure_default_license()

        if mode == "data_only":
            await _apply_auth_snapshot(auth_snapshot)
        elif mode == "full_restore_reset_admin":
            if not new_admin_username or not new_admin_password:
                raise ValueError("New admin username and password are required for reset mode")
            await save_admin_credentials(new_admin_username.strip(), hash_password(new_admin_password))
            existing = await db.get_user_by_username(new_admin_username.strip())
            if existing:
                await db.update_user_password(
                    int(existing["id"]),
                    hash_password(new_admin_password),
                    must_change_password=False,
                )
                await db.update_user(
                    int(existing["id"]),
                    role="super_admin",
                    is_active=True,
                    is_superadmin=True,
                )
            else:
                await db.create_user(
                    username=new_admin_username.strip(),
                    password_hash=hash_password(new_admin_password),
                    full_name="DitakNet Owner",
                    role="super_admin",
                    is_active=True,
                    is_superadmin=True,
                    must_change_password=False,
                )
            await complete_setup()

        if mode == "full_restore":
            await db.set_app_setting("setup_complete", "1")

        try:
            from ditaknet.api.deps import get_alert_engine, get_scheduler

            get_alert_engine().clear_runtime_state()
            sched = get_scheduler()
            if getattr(sched, "_scheduler", None):
                sched._scheduler.resume()
            await sched.reload_services()
        except Exception as exc:
            logger.debug("Scheduler reload after restore: {}", exc)

        await db.create_audit_log(
            "backup.restore",
            actor=actor,
            resource="backup",
            resource_id=filename,
            detail=f"mode={mode};pre_restore={pre_restore['filename']}",
        )

        return {
            "ok": True,
            "mode": mode,
            "restored_from": filename,
            "pre_restore_backup": pre_restore["filename"],
            "validation": validation,
        }
    except Exception as exc:
        logger.exception("Restore failed for {}: {}", filename, exc)
        if rollback_db and rollback_db.exists():
            try:
                await db.close_db()
                for path in _sqlite_sidecars(target_db):
                    path.unlink(missing_ok=True)
                shutil.copy2(rollback_db, target_db)
                await db.init_db(str(target_db))
            except Exception as rollback_exc:
                logger.error("Rollback after failed restore also failed: {}", rollback_exc)
        if scheduler and getattr(scheduler, "_scheduler", None):
            try:
                scheduler._scheduler.resume()
            except Exception:
                pass
        raise


async def restore_from_uploaded_file(
    uploaded_path: Path,
    *,
    mode: RestoreMode,
    confirm: bool,
    new_admin_username: str | None = None,
    new_admin_password: str | None = None,
    actor: str = "setup",
) -> dict[str, Any]:
    dest = backup_root() / uploaded_path.name
    shutil.copy2(uploaded_path, dest)
    return await restore_from_backup(
        dest.name,
        mode=mode,
        confirm=confirm,
        new_admin_username=new_admin_username,
        new_admin_password=new_admin_password,
        actor=actor,
    )
