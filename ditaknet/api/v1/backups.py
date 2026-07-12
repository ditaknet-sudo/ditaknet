"""Backup API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ditaknet import database as db
from ditaknet.core.backup import create_backup, list_backups
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/backups", tags=["backups"])


@router.get("")
async def list_database_backups(
    user: AuthenticatedUser = Depends(require_permissions("admin")),
) -> dict:
    return {"backups": list_backups()}


@router.post("")
async def create_database_backup(
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("admin")),
) -> dict:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    try:
        backup = create_backup(payload.get("filename") if isinstance(payload, dict) else None)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    try:
        await db.create_audit_log(
            "backup.create",
            actor=user.username,
            resource="backup",
            resource_id=backup["filename"],
            ip_address=request.client.host if request.client else "",
        )
    except Exception:
        pass
    return backup
