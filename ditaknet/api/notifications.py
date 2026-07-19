"""Notification center API."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from ditaknet import database as db
from ditaknet.core.system_log_service import redact_text
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _serialize(row: dict) -> dict:
    meta = {}
    try:
        meta = json.loads(row.get("metadata_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        meta = {}
    return {
        "id": row.get("id"),
        "created_at": row.get("created_at"),
        "level": row.get("level"),
        "category": row.get("category"),
        "title": row.get("title"),
        "message": redact_text(str(row.get("message") or "")),
        "action_url": row.get("action_url") or "",
        "read_at": row.get("read_at"),
        "dismissed_at": row.get("dismissed_at"),
        "metadata": meta,
        "unread": row.get("read_at") is None and row.get("dismissed_at") is None,
    }


@router.get("")
async def list_notifications_api(
    user: AuthenticatedUser = Depends(require_permissions("read")),
) -> dict:
    rows = await db.list_notifications(limit=100)
    return {"notifications": [_serialize(r) for r in rows]}


@router.get("/unread-count")
async def unread_count(
    user: AuthenticatedUser = Depends(require_permissions("read")),
) -> dict:
    return {"count": await db.count_unread_notifications()}


@router.post("/{notification_id}/read")
async def mark_read(
    notification_id: int,
    user: AuthenticatedUser = Depends(require_permissions("read")),
) -> dict:
    row = await db.mark_notification_read(notification_id)
    if not row:
        raise HTTPException(status_code=404, detail="Notification not found")
    return _serialize(row)


@router.post("/read-all")
async def mark_all_read(
    user: AuthenticatedUser = Depends(require_permissions("read")),
) -> dict:
    count = await db.mark_all_notifications_read()
    return {"updated": count}


@router.post("/{notification_id}/dismiss")
async def dismiss(
    notification_id: int,
    user: AuthenticatedUser = Depends(require_permissions("read")),
) -> dict:
    row = await db.dismiss_notification(notification_id)
    if not row:
        raise HTTPException(status_code=404, detail="Notification not found")
    return _serialize(row)
