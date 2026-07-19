"""Admin notification center with deduplication."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ditaknet import database as db
from ditaknet.core.system_log_service import redact_text, record


async def notify(
    *,
    level: str,
    category: str,
    title: str,
    message: str,
    action_url: str = "",
    metadata: dict[str, Any] | None = None,
    dedupe_key: str = "",
    log_event: bool = True,
) -> dict:
    safe_message = redact_text(message)
    safe_title = redact_text(title)
    row = await db.create_notification(
        level=level,
        category=category,
        title=safe_title,
        message=safe_message,
        action_url=action_url,
        metadata_json=json.dumps(metadata or {}),
        dedupe_key=dedupe_key,
    )
    if log_event:
        try:
            await record(
                level if level in {"debug", "info", "warning", "error", "critical"} else "info",
                category if category in {"application", "monitoring", "discovery", "notification", "license", "security", "audit", "system"} else "system",
                "notification_created",
                safe_title,
                metadata={"notification_id": row.get("id"), "category": category},
            )
        except Exception:
            pass
    return row


async def notify_update_available(payload: dict[str, Any]) -> None:
    if not payload.get("update_available"):
        return
    latest = payload.get("latest_version") or payload.get("latest_image_tag") or "new"
    await notify(
        level="info",
        category="update",
        title="New version available",
        message=f"Version {latest} is available. Create a backup before updating.",
        action_url="/settings/updates",
        dedupe_key=f"update:{latest}",
        metadata={"latest_version": latest},
    )


async def notify_customer_notices(payload: dict[str, Any]) -> None:
    """Emit deduplicated notifications for manifest announcements and promotions."""
    if not payload.get("customer_notice_available"):
        return
    for bucket, items in (
        ("announcement", payload.get("announcements") or []),
        ("promotion", payload.get("promotions") or []),
    ):
        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            message = str(item.get("message") or "").strip()
            if not title and not message:
                continue
            notice_id = str(item.get("id") or "").strip() or hashlib.sha256(
                f"{bucket}:{title}:{message}".encode("utf-8")
            ).hexdigest()[:16]
            level = str(item.get("level") or "info").strip().lower()
            if level not in {"info", "success", "warning", "error"}:
                level = "info"
            await notify(
                level=level,
                category="update",
                title=title or ("Promotion" if bucket == "promotion" else "Announcement"),
                message=message or title,
                action_url=str(item.get("url") or "").strip() or "/settings/updates",
                dedupe_key=f"update:{bucket}:{notice_id}",
                metadata={"notice_id": notice_id, "notice_type": bucket},
            )


async def notify_update_check_failed(
    payload: dict[str, Any] | None = None,
    *,
    not_configured: bool = False,
) -> None:
    if not_configured:
        await notify(
            level="warning",
            category="update",
            title="Update source not configured",
            message="Configure GitHub/GHCR image source to check for updates.",
            action_url="/settings/updates",
            dedupe_key="update:source_not_configured",
        )
        return
    await notify(
        level="warning",
        category="update",
        title="Update check failed",
        message="Could not check for updates. Verify network and update source settings.",
        action_url="/settings/updates",
        dedupe_key="update:check_failed",
    )


async def notify_backup_result(*, success: bool, filename: str, detail: str = "") -> None:
    await notify(
        level="success" if success else "error",
        category="backup",
        title="Backup created" if success else "Backup failed",
        message=detail or filename,
        action_url="/settings/backups",
        dedupe_key="" if success else "backup:failed",
    )


async def notify_restore_result(*, success: bool, filename: str, detail: str = "") -> None:
    await notify(
        level="success" if success else "error",
        category="restore",
        title="Restore completed" if success else "Restore failed",
        message=detail or filename,
        action_url="/settings/backups",
        dedupe_key="" if success else "restore:failed",
    )


async def notify_system_problem(title: str, message: str, *, dedupe_key: str, level: str = "warning") -> None:
    await notify(
        level=level,
        category="system",
        title=title,
        message=message,
        action_url="/system/activity",
        dedupe_key=dedupe_key,
    )
