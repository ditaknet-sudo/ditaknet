"""Backup management API (/api/backups)."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ditaknet import database as db
from ditaknet.core.backup import (
    backup_root,
    create_full_backup,
    delete_backup,
    list_backups,
    resolve_backup_path,
    validate_backup_file,
)
from ditaknet.core.notifications_service import notify_backup_result, notify_restore_result
from ditaknet.core.restore import RestoreMode, restore_from_backup
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/backups", tags=["backups"])


class RestoreRequest(BaseModel):
    mode: RestoreMode = "full_restore"
    confirm: bool = False
    new_admin_username: str | None = None
    new_admin_password: str | None = None


@router.get("")
async def list_backup_files(
    user: AuthenticatedUser = Depends(require_permissions("backups.view")),
) -> dict:
    return {"backups": list_backups()}


@router.post("/create")
async def create_backup_file(
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("backups.create")),
) -> dict:
    try:
        backup = await create_full_backup()
        await notify_backup_result(success=True, filename=backup["filename"])
        await db.create_audit_log(
            "backup.create",
            actor=user.username,
            resource="backup",
            resource_id=backup["filename"],
            ip_address=request.client.host if request.client else "",
        )
        return backup
    except Exception as exc:
        await notify_backup_result(success=False, filename="", detail=str(exc))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.get("/{filename}/download")
async def download_backup(
    filename: str,
    user: AuthenticatedUser = Depends(require_permissions("backups.download")),
):
    try:
        path = resolve_backup_path(filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="Backup not found")
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@router.post("/upload")
async def upload_backup(
    request: Request,
    file: UploadFile = File(...),
    user: AuthenticatedUser = Depends(require_permissions("backups.restore")),
) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".zip", ".sqlite3", ".db", ".sqlite"}:
        raise HTTPException(status_code=400, detail="Unsupported backup file type")
    dest = backup_root() / Path(file.filename or "uploaded-backup").name
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    try:
        validation = validate_backup_file(dest.name)
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.create_audit_log(
        "backup.upload",
        actor=user.username,
        resource="backup",
        resource_id=dest.name,
        ip_address=request.client.host if request.client else "",
    )
    return {"filename": dest.name, "validation": validation}


@router.post("/{filename}/validate")
async def validate_backup(
    filename: str,
    user: AuthenticatedUser = Depends(require_permissions("backups.view")),
) -> dict:
    try:
        return validate_backup_file(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{filename}/restore")
async def restore_backup(
    filename: str,
    body: RestoreRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("backups.restore")),
) -> dict:
    try:
        result = await restore_from_backup(
            filename,
            mode=body.mode,
            confirm=body.confirm,
            new_admin_username=body.new_admin_username,
            new_admin_password=body.new_admin_password,
            actor=user.username,
        )
        await notify_restore_result(success=True, filename=filename)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        await notify_restore_result(success=False, filename=filename, detail=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/{filename}")
async def remove_backup(
    filename: str,
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("backups.delete")),
) -> dict:
    try:
        delete_backup(filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.create_audit_log(
        "backup.delete",
        actor=user.username,
        resource="backup",
        resource_id=filename,
        ip_address=request.client.host if request.client else "",
    )
    return {"ok": True, "filename": filename}
