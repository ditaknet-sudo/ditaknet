"""Admin-confirmed, backup-first handoff for external container updates.

DitakNet deliberately never controls Docker or the TrueNAS Apps service.  This
module verifies trusted release metadata, compatibility and a fresh recovery
point before returning exact operator commands for the external redeploy.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from ditaknet import database as db
from ditaknet.config import settings
from ditaknet.core.backup import (
    BACKUP_OPERATION_LOCK,
    FORMAT_VERSION,
    create_full_backup,
    validate_backup_file,
)
from ditaknet.core.restore import offline_restore_command
from ditaknet.core.updates import compare_versions, get_update_status, parse_semver


_RECEIPT_KEY = "update_preflight_receipt_json"
_LOCK = asyncio.Lock()
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_OFFICIAL_IMAGE = re.compile(
    r"^ghcr\.io/ditaknet-sudo/ditaknet:(?P<version>[0-9A-Za-z.-]+)$"
)
_RECEIPT_TTL_HOURS = 2


class UpdatePreflightError(ValueError):
    """A safe, actionable reason why an update handoff was refused."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _version_text(value: Any) -> str:
    return str(value or "").strip().removeprefix("v")


def evaluate_update_compatibility(payload: dict[str, Any]) -> dict[str, Any]:
    """Evaluate the signed manifest's source-to-target compatibility contract."""
    current = _version_text(payload.get("current_version") or settings.app_version)
    target = _version_text(payload.get("latest_version"))
    contract = payload.get("compatibility")
    if not isinstance(contract, dict):
        contract = {}

    reasons: list[str] = []
    current_parsed = parse_semver(current)
    target_parsed = parse_semver(target)
    if current_parsed is None:
        reasons.append("Current application version is not valid SemVer")
    if target_parsed is None:
        reasons.append("Target application version is not valid SemVer")
    if current_parsed and target_parsed and compare_versions(target, current) <= 0:
        reasons.append("Target version is not newer than the current version")

    minimum = _version_text(
        contract.get("minimum_current_version")
        or payload.get("minimum_supported_version")
    )
    maximum = _version_text(contract.get("maximum_current_version"))
    if minimum and (
        parse_semver(minimum) is None or compare_versions(current, minimum) < 0
    ):
        reasons.append(f"Direct update requires current version {minimum} or newer")
    if maximum and (
        parse_semver(maximum) is None or compare_versions(current, maximum) > 0
    ):
        reasons.append(f"Direct update supports current version {maximum} or older")

    allow_major = contract.get("allow_major_upgrade") is True
    if (
        current_parsed
        and target_parsed
        and current_parsed[0] != target_parsed[0]
        and not allow_major
    ):
        reasons.append("Cross-major update is not allowed by this manifest")

    requires_backup = contract.get("requires_backup") is True
    if not requires_backup:
        reasons.append("Manifest does not require a recovery backup")

    schema_revision = contract.get("target_schema_revision")
    if (
        isinstance(schema_revision, bool)
        or not isinstance(schema_revision, int)
        or schema_revision < 1
    ):
        reasons.append("Manifest target schema revision is missing or invalid")
    elif schema_revision < db.DATABASE_SCHEMA_REVISION:
        reasons.append(
            "Manifest target schema revision is older than this database reader"
        )

    rollback_policy = str(contract.get("rollback_policy") or "").strip()
    if rollback_policy not in {"state_restore_required", "unsupported"}:
        reasons.append("Manifest rollback policy is missing or invalid")
    elif rollback_policy == "unsupported":
        reasons.append("This update does not declare a supported rollback path")

    backup_format_version = contract.get("backup_format_version")
    if backup_format_version != FORMAT_VERSION:
        reasons.append(
            f"Manifest requires backup format {backup_format_version}; this instance creates format {FORMAT_VERSION}"
        )

    return {
        "compatible": not reasons,
        "reasons": reasons,
        "current_version": current,
        "target_version": target,
        "minimum_current_version": minimum or None,
        "maximum_current_version": maximum or None,
        "target_schema_revision": schema_revision,
        "requires_backup": requires_backup,
        "rollback_policy": rollback_policy or None,
        "allow_major_upgrade": allow_major,
        "backup_format_version": backup_format_version,
    }


def _require_bound_preflight_backup(
    validation: dict[str, Any],
    *,
    expected_sha256: str,
    expected_context: dict[str, Any],
) -> None:
    if validation.get("valid") is not True:
        raise UpdatePreflightError(
            "backup_validation_failed",
            "The pre-update backup did not pass integrity validation",
        )
    if validation.get("sha256") != expected_sha256:
        raise UpdatePreflightError(
            "backup_validation_failed",
            "The pre-update backup archive digest changed",
        )
    if validation.get("format_version") != FORMAT_VERSION:
        raise UpdatePreflightError(
            "backup_format_mismatch",
            "The recovery point does not use the required backup format",
        )
    if validation.get("backup_origin") != "pre_update":
        raise UpdatePreflightError(
            "backup_context_mismatch",
            "The recovery point is not marked as a pre-update backup",
        )
    actual_context = validation.get("operation_context")
    if not isinstance(actual_context, dict):
        actual_context = {}
    for key, expected in expected_context.items():
        if expected is not None and actual_context.get(key) != expected:
            raise UpdatePreflightError(
                "backup_context_mismatch",
                f"The recovery point is not bound to the verified update ({key})",
            )


def _validate_release_identity(payload: dict[str, Any], target: str) -> tuple[str, str]:
    if payload.get("manifest_trusted") is not True:
        raise UpdatePreflightError(
            "manifest_untrusted",
            "Update manifest is not verified by a trusted signing key",
        )
    if payload.get("source") != "manifest":
        raise UpdatePreflightError(
            "manifest_not_fresh",
            "A fresh signed manifest check is required before update handoff",
        )
    if int(payload.get("schema_version") or 0) < 2:
        raise UpdatePreflightError(
            "manifest_schema_unsupported",
            "Update manifest schema is not safe for managed handoff",
        )
    if not payload.get("update_available"):
        raise UpdatePreflightError("update_unavailable", "No newer update is available")

    offered = _version_text(payload.get("latest_version"))
    if target != offered:
        raise UpdatePreflightError(
            "target_changed",
            "Requested target no longer matches the verified update manifest",
        )

    image = str(payload.get("docker_image") or "").strip().lower()
    match = _OFFICIAL_IMAGE.fullmatch(image)
    if not match or _version_text(match.group("version")) != target:
        raise UpdatePreflightError(
            "image_mismatch",
            "Manifest image repository/tag does not match the target version",
        )
    digest = str(payload.get("image_digest") or "").strip().lower()
    if not _DIGEST.fullmatch(digest):
        raise UpdatePreflightError(
            "digest_missing",
            "Manifest does not contain a valid immutable image digest",
        )
    return image, digest


def _handoff_commands(
    *,
    image: str,
    digest: str,
    target: str,
    current: str,
    rollback_policy: str,
    backup_filename: str,
    backup_sha256: str,
) -> dict[str, Any]:
    repository = image.rsplit(":", 1)[0]
    digest_ref = f"{repository}@{digest}"
    docker = "\n".join(
        [
            f'docker pull "{image}"',
            (
                f'docker image inspect --format "{{{{join .RepoDigests " "}}}}" "{image}" '
                f'| grep -F "{digest_ref}"'
            ),
            f"# Set DITAKNET_VERSION={target} in the deployment environment.",
            "docker compose up -d",
            "curl -fsS http://127.0.0.1:5833/health/deep",
            f"# Verify JSON fields: status=healthy, version={target}, schema revision expected.",
        ]
    )
    rollback = [f"# Roll back only after a failed {target} deep-health check."]
    if rollback_policy == "state_restore_required":
        rollback.extend(
            [
                "# Keep the failed/new image selected for the one-shot restore tool.",
                "docker compose stop ditaknet",
                f"# Restore validated pre-update state: {backup_filename}",
                offline_restore_command(backup_filename, backup_sha256),
                f"# Only now set DITAKNET_VERSION={current} and start the old image.",
                "docker compose up -d",
            ]
        )
    else:
        rollback.append(
            "# Automatic rollback is unsupported; keep the service stopped and follow the release runbook."
        )
    rollback.append("curl -fsS http://127.0.0.1:5833/health/deep")
    truenas = [
        "Keep the validated DitakNet backup and record a recursive ZFS snapshot covering every mounted DitakNet dataset before redeploying.",
        f"Edit the DitakNet App image tag to the exact version {target}; never use latest.",
        f"Confirm the pulled image resolves to {digest}.",
        "Wait for the App to become Running, then verify /health/deep.",
    ]
    if rollback_policy == "state_restore_required":
        truenas.extend(
            [
                "Rollback order: stop the DitakNet App and do not start the previous image yet.",
                "Clone or roll back the recorded recursive pre-update ZFS snapshot for all mounted DitakNet datasets while the App remains stopped.",
                f"Only after state recovery, select the previous exact image tag {current} and start the App.",
                "Verify the previous version, schema compatibility, login, data, and /health/deep before making the recovered App authoritative.",
                f"If using the backup instead of ZFS, run the documented one-shot maintenance container from the failed/new image with backup {backup_filename} (SHA-256 {backup_sha256}) and the same Data/Backups mounts before selecting the previous image.",
            ]
        )
    else:
        truenas.append(
            "Rollback is unsupported; keep the App stopped and follow the release runbook."
        )
    return {
        "docker_compose": docker,
        "docker_pull": f'docker pull "{digest_ref}"',
        "rollback": "\n".join(rollback),
        "truenas": truenas,
    }


async def prepare_update(
    *,
    target_version: str,
    confirmation: str,
    actor: str,
    ip_address: str = "",
) -> dict[str, Any]:
    """Return an auditable external-update receipt only after a valid backup."""
    target = _version_text(target_version)
    if parse_semver(target) is None:
        raise UpdatePreflightError("invalid_target", "Target version is invalid")
    expected_confirmation = f"UPDATE {target}"
    if secrets.compare_digest(str(confirmation or ""), expected_confirmation) is False:
        raise UpdatePreflightError(
            "confirmation_required",
            f"Confirmation must exactly match {expected_confirmation}",
        )

    async with _LOCK, BACKUP_OPERATION_LOCK:
        payload = await get_update_status(force=True)
        image, digest = _validate_release_identity(payload, target)
        compatibility = evaluate_update_compatibility(payload)
        if not compatibility["compatible"]:
            raise UpdatePreflightError(
                "incompatible_update",
                "; ".join(compatibility["reasons"]),
            )

        current = compatibility["current_version"]
        context = {
            "source_version": current,
            "source_image_tag": payload.get("current_image_tag"),
            "target_version": target,
            "target_image": image,
            "target_digest": digest,
            "target_schema_revision": compatibility["target_schema_revision"],
            "rollback_policy": compatibility["rollback_policy"],
            "update_channel": payload.get("channel"),
            "manifest_sequence": payload.get("sequence"),
        }
        backup = await create_full_backup(
            backup_origin="pre_update",
            operation_context=context,
        )
        validation = validate_backup_file(backup["filename"])
        _require_bound_preflight_backup(
            validation,
            expected_sha256=str(backup.get("sha256") or ""),
            expected_context=context,
        )

        created_at = datetime.now(UTC)
        expires_at = created_at + timedelta(hours=_RECEIPT_TTL_HOURS)
        commands = _handoff_commands(
            image=image,
            digest=digest,
            target=target,
            current=current,
            rollback_policy=str(compatibility["rollback_policy"]),
            backup_filename=backup["filename"],
            backup_sha256=str(validation["sha256"]),
        )
        receipt = {
            "status": "ready",
            "receipt_id": secrets.token_urlsafe(24),
            "created_at": created_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "actor": actor,
            "channel": payload.get("channel"),
            "manifest_sequence": payload.get("sequence"),
            "signing_key_id": payload.get("signing_key_id"),
            "current_version": current,
            "target_version": target,
            "docker_image": image,
            "image_digest": digest,
            "compatibility": compatibility,
            "backup": {
                "filename": backup["filename"],
                "sha256": backup["sha256"],
                "validated": True,
                "created_at": backup["created_at"],
                "format_version": validation["format_version"],
                "backup_origin": validation["backup_origin"],
                "operation_context": validation["operation_context"],
            },
            "commands": commands,
        }
        try:
            await db.create_audit_log(
                "update.preflight.ready",
                actor=actor,
                resource="update",
                resource_id=target,
                detail=(
                    f"receipt={receipt['receipt_id']} digest={digest} "
                    f"backup={backup['filename']} backup_sha256={backup['sha256']}"
                ),
                ip_address=ip_address,
            )
            await db.set_app_setting(
                _RECEIPT_KEY, json.dumps(receipt, ensure_ascii=False)
            )
        except Exception as exc:
            raise UpdatePreflightError(
                "receipt_persistence_failed",
                "The validated backup was created, but the auditable update receipt could not be stored",
            ) from exc
        return receipt


async def get_last_update_preflight() -> dict[str, Any] | None:
    raw = await db.get_app_setting(_RECEIPT_KEY, "") or ""
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        expires = datetime.fromisoformat(
            str(payload.get("expires_at") or "").replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return None
    if expires.tzinfo is None or expires.utcoffset() is None:
        return None
    payload["expired"] = datetime.now(UTC) >= expires
    if payload["expired"] or payload.get("status") != "ready":
        return payload

    backup = payload.get("backup")
    compatibility = payload.get("compatibility")
    if not isinstance(backup, dict) or not isinstance(compatibility, dict):
        payload.update({"status": "invalid", "expired": True})
        return payload
    expected_context = {
        "source_version": payload.get("current_version"),
        "target_version": payload.get("target_version"),
        "target_image": payload.get("docker_image"),
        "target_digest": payload.get("image_digest"),
        "target_schema_revision": compatibility.get("target_schema_revision"),
        "rollback_policy": compatibility.get("rollback_policy"),
        "update_channel": payload.get("channel"),
        "manifest_sequence": payload.get("manifest_sequence"),
    }
    try:
        validation = validate_backup_file(str(backup.get("filename") or ""))
        _require_bound_preflight_backup(
            validation,
            expected_sha256=str(backup.get("sha256") or ""),
            expected_context=expected_context,
        )
    except (FileNotFoundError, ValueError, UpdatePreflightError):
        payload.update({"status": "invalid", "expired": True})
    return payload
