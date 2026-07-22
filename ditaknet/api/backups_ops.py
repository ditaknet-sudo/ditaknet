"""Backup management API (/api/backups)."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ditaknet import database as db
from ditaknet.core.backup import (
    BACKUP_OPERATION_LOCK,
    MAX_BACKUP_FILE_BYTES,
    backup_root,
    create_full_backup,
    delete_backup,
    list_backups,
    resolve_backup_path,
    validate_backup_file,
)
from ditaknet.core.notifications_service import (
    notify_backup_result,
    notify_restore_result,
)
from ditaknet.core.restore import (
    OfflineRestoreRequired,
    offline_restore_command,
    restore_from_backup,
)
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/backups", tags=["backups"])


async def _serialize_backup_mutation():
    async with BACKUP_OPERATION_LOCK:
        yield


class RestoreRequest(BaseModel):
    confirm: bool = False


@router.get("")
async def list_backup_files(
    user: AuthenticatedUser = Depends(require_permissions("backups.view")),
) -> dict:
    return {"backups": list_backups()}


@router.post("/create")
async def create_backup_file(
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("backups.create")),
    _operation_guard: None = Depends(_serialize_backup_mutation),
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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc


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
    _operation_guard: None = Depends(_serialize_backup_mutation),
) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".zip", ".sqlite3", ".db", ".sqlite"}:
        raise HTTPException(status_code=400, detail="Unsupported backup file type")
    filename = Path(file.filename or f"uploaded-backup{suffix}").name
    dest = resolve_backup_path(filename, zip_backup=suffix == ".zip")
    if dest.exists():
        raise HTTPException(
            status_code=409, detail="A backup with this name already exists"
        )

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".ditaknet-upload-",
        suffix=suffix,
        dir=backup_root(),
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        total = 0
        with temporary_path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_BACKUP_FILE_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail="Backup upload exceeds the supported size limit",
                    )
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())

        validation = await asyncio.to_thread(validate_backup_file, temporary_path.name)
        try:
            # A same-directory hard link is atomic and never overwrites an
            # existing recovery point. Remove the private temporary name only
            # after the validated inode has its final collision-safe name.
            os.link(temporary_path, dest)
        except FileExistsError as exc:
            raise HTTPException(
                status_code=409,
                detail="A backup with this name already exists",
            ) from exc
        validation["filename"] = dest.name
    except Exception as exc:
        temporary_path.unlink(missing_ok=True)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    temporary_path.unlink(missing_ok=True)
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
        validation = await asyncio.to_thread(validate_backup_file, filename)
        validation["offline_restore_required"] = True
        validation["offline_restore_command"] = offline_restore_command(
            filename, str(validation["sha256"])
        )
        return validation
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
            confirm=body.confirm,
            actor=user.username,
        )
        await notify_restore_result(success=True, filename=filename)
        return result
    except OfflineRestoreRequired as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
            headers={"X-DitakNet-Restore-Mode": "offline-required"},
        ) from exc
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
    _operation_guard: None = Depends(_serialize_backup_mutation),
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
