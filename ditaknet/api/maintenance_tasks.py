"""Maintenance planning tasks — reduce on-site visits with tracked follow-ups."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ditaknet import database as db
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/maintenance", tags=["maintenance-tasks"])


@router.get("")
async def list_tasks(
    status: str | None = None,
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    return {"tasks": await db.list_maintenance_tasks(status=status)}


@router.get("/{task_id}")
async def get_task(
    task_id: int,
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    task = await db.get_maintenance_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/{task_id}/resolve")
async def resolve_task(
    task_id: int,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    task = await db.resolve_maintenance_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await db.create_audit_log(
        "maintenance.resolve",
        actor=user.username,
        resource="maintenance_task",
        resource_id=str(task_id),
    )
    return task
