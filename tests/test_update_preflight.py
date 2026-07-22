"""Backup-first compatibility and update handoff regression tests."""

from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from ditaknet import database as db
from ditaknet.core import backup, update_preflight
from ditaknet.api.v1.system import system_router
from ditaknet.security import AuthenticatedUser


def _trusted_status(**overrides: object) -> dict[str, object]:
    return {
        "schema_version": 2,
        "source": "manifest",
        "manifest_trusted": True,
        "signing_key_id": "stable-2026",
        "sequence": 2001001,
        "channel": "stable",
        "current_version": "2.0.1",
        "current_image_tag": "2.0.1",
        "latest_version": "2.1.0",
        "update_available": True,
        "minimum_supported_version": "2.0.0",
        "docker_image": "ghcr.io/ditaknet-sudo/ditaknet:2.1.0",
        "image_digest": "sha256:" + "a" * 64,
        "compatibility": {
            "minimum_current_version": "2.0.0",
            "maximum_current_version": "2.0.99",
            "requires_backup": True,
            "allow_major_upgrade": False,
            "target_schema_revision": 2,
            "backup_format_version": backup.FORMAT_VERSION,
            "rollback_policy": "state_restore_required",
        },
        **overrides,
    }


def test_compatibility_contract_blocks_unsupported_sources_and_major_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    too_old = update_preflight.evaluate_update_compatibility(
        _trusted_status(current_version="1.9.9")
    )
    assert too_old["compatible"] is False
    assert any("2.0.0 or newer" in reason for reason in too_old["reasons"])

    major = update_preflight.evaluate_update_compatibility(
        _trusted_status(latest_version="3.0.0")
    )
    assert major["compatible"] is False
    assert any("Cross-major" in reason for reason in major["reasons"])

    monkeypatch.setattr(db, "DATABASE_SCHEMA_REVISION", 2)
    older_schema = update_preflight.evaluate_update_compatibility(
        _trusted_status(
            compatibility={
                **_trusted_status()["compatibility"],
                "target_schema_revision": db.DATABASE_SCHEMA_REVISION - 1,
            }
        )
    )
    assert older_schema["compatible"] is False
    assert any(
        "older than this database reader" in reason
        for reason in older_schema["reasons"]
    )

    image_only_schema_change = update_preflight.evaluate_update_compatibility(
        _trusted_status(
            compatibility={
                **_trusted_status()["compatibility"],
                "target_schema_revision": db.DATABASE_SCHEMA_REVISION + 1,
                "rollback_policy": "image_only",
            }
        )
    )
    assert image_only_schema_change["compatible"] is False
    assert any(
        "rollback policy is missing or invalid" in reason
        for reason in image_only_schema_change["reasons"]
    )


def test_preflight_refuses_untrusted_metadata_before_backup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        update_preflight,
        "get_update_status",
        AsyncMock(return_value=_trusted_status(manifest_trusted=False)),
    )
    create = AsyncMock()
    monkeypatch.setattr(update_preflight, "create_full_backup", create)

    with pytest.raises(update_preflight.UpdatePreflightError) as caught:
        asyncio.run(
            update_preflight.prepare_update(
                target_version="2.1.0",
                confirmation="UPDATE 2.1.0",
                actor="admin",
            )
        )

    assert caught.value.code == "manifest_untrusted"
    create.assert_not_awaited()


def test_preflight_requires_exact_confirmation_before_network_or_backup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = AsyncMock()
    monkeypatch.setattr(update_preflight, "get_update_status", status)

    with pytest.raises(update_preflight.UpdatePreflightError) as caught:
        asyncio.run(
            update_preflight.prepare_update(
                target_version="2.1.0",
                confirmation="yes",
                actor="admin",
            )
        )

    assert caught.value.code == "confirmation_required"
    status.assert_not_awaited()


def test_preflight_creates_validated_digest_bound_recovery_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_database = tmp_path / "runtime" / "live.db"
    backup_directory = tmp_path / "backups"
    monkeypatch.setattr(backup.settings, "backup_dir", str(backup_directory))
    monkeypatch.setattr(
        update_preflight,
        "get_update_status",
        AsyncMock(return_value=_trusted_status()),
    )

    async def exercise() -> None:
        await db.close_db()
        try:
            await db.init_db(str(live_database))
            receipt = await update_preflight.prepare_update(
                target_version="2.1.0",
                confirmation="UPDATE 2.1.0",
                actor="admin",
                ip_address="127.0.0.1",
            )

            assert receipt["status"] == "ready"
            assert receipt["target_version"] == "2.1.0"
            assert receipt["image_digest"] == "sha256:" + "a" * 64
            assert receipt["backup"]["validated"] is True
            assert len(receipt["backup"]["sha256"]) == 64
            assert "docker compose up -d" in receipt["commands"]["docker_compose"]
            assert "2.0.1" in receipt["commands"]["rollback"]
            rollback = receipt["commands"]["rollback"]
            assert "Settings > Backups" not in rollback
            assert "-m ditaknet.offline_restore" in rollback
            assert receipt["backup"]["filename"] in rollback
            assert receipt["backup"]["sha256"] in rollback
            assert "--confirm 'RESTORE " in rollback
            assert rollback.index("docker compose stop ditaknet") < rollback.index(
                "-m ditaknet.offline_restore"
            )
            assert rollback.index("-m ditaknet.offline_restore") < rollback.index(
                "Only now set DITAKNET_VERSION=2.0.1"
            )
            truenas = "\n".join(receipt["commands"]["truenas"])
            assert truenas.index("stop the DitakNet App") < truenas.index(
                "Clone or roll back"
            )
            assert truenas.index("Clone or roll back") < truenas.index(
                "previous exact image tag 2.0.1"
            )
            assert receipt["backup"]["filename"] in truenas
            assert receipt["backup"]["sha256"] in truenas

            validated = backup.validate_backup_file(receipt["backup"]["filename"])
            assert validated["sha256"] == receipt["backup"]["sha256"]
            assert validated["backup_origin"] == "pre_update"
            assert validated["operation_context"]["target_version"] == "2.1.0"
            assert (
                validated["operation_context"]["target_digest"]
                == receipt["image_digest"]
            )

            persisted = await update_preflight.get_last_update_preflight()
            assert persisted is not None
            assert persisted["receipt_id"] == receipt["receipt_id"]
            assert persisted["expired"] is False

            backup_path = backup_directory / receipt["backup"]["filename"]
            backup_path.write_bytes(backup_path.read_bytes() + b"tamper")
            invalidated = await update_preflight.get_last_update_preflight()
            assert invalidated is not None
            assert invalidated["status"] == "invalid"
            assert invalidated["expired"] is True
        finally:
            await db.close_db()

    asyncio.run(exercise())


def test_last_preflight_rejects_naive_expiry_timestamp(
    tmp_path: Path,
) -> None:
    live_database = tmp_path / "runtime" / "live.db"

    async def exercise() -> None:
        await db.close_db()
        try:
            await db.init_db(str(live_database))
            await db.set_app_setting(
                "update_preflight_receipt_json",
                json.dumps(
                    {
                        "status": "ready",
                        "expires_at": "2099-01-01T00:00:00",
                    }
                ),
            )
            assert await update_preflight.get_last_update_preflight() is None
        finally:
            await db.close_db()

    asyncio.run(exercise())


def test_preflight_backup_binding_rejects_wrong_origin() -> None:
    validation = {
        "valid": True,
        "sha256": "a" * 64,
        "format_version": backup.FORMAT_VERSION,
        "backup_origin": "manual",
        "operation_context": {"target_version": "2.1.0"},
    }
    with pytest.raises(update_preflight.UpdatePreflightError) as caught:
        update_preflight._require_bound_preflight_backup(
            validation,
            expected_sha256="a" * 64,
            expected_context={"target_version": "2.1.0"},
        )
    assert caught.value.code == "backup_context_mismatch"


def test_backup_member_digest_detects_post_creation_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_database = tmp_path / "runtime" / "live.db"
    backup_directory = tmp_path / "backups"
    monkeypatch.setattr(backup.settings, "backup_dir", str(backup_directory))

    async def create() -> Path:
        await db.close_db()
        try:
            await db.init_db(str(live_database))
            result = await backup.create_full_backup("tamper-check.zip")
            return Path(result["path"])
        finally:
            await db.close_db()

    archive_path = asyncio.run(create())
    replacement = tmp_path / "replacement.zip"
    with (
        zipfile.ZipFile(archive_path) as source,
        zipfile.ZipFile(replacement, "w", compression=zipfile.ZIP_DEFLATED) as target,
    ):
        for name in source.namelist():
            content = source.read(name)
            if name == "settings.json":
                content = json.dumps({"tampered": True}).encode("utf-8")
            target.writestr(name, content)
    replacement.replace(archive_path)

    with pytest.raises(ValueError, match="checksum mismatch"):
        backup.validate_backup_file(archive_path.name)


def test_update_preflight_api_route_requires_admin_permission() -> None:
    route = next(
        item
        for item in system_router.routes
        if getattr(item, "path", "") == "/system/update-preflight"
        and "POST" in getattr(item, "methods", set())
    )
    permission_dependency = route.dependant.dependencies[0].call
    viewer = AuthenticatedUser(username="viewer", role="viewer")
    admin = AuthenticatedUser(username="admin", role="admin")

    with pytest.raises(HTTPException) as denied:
        asyncio.run(permission_dependency(viewer))
    assert denied.value.status_code == 403
    assert asyncio.run(permission_dependency(admin)) is admin
