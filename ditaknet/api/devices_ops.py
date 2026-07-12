"""Device operations: apply profile, set parent (topology)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ditaknet import database as db
from ditaknet.core.licensing import LicenseLimitError
from ditaknet.core.profile_apply import apply_profile_to_host
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/devices", tags=["device-operations"])


class ApplyProfileBody(BaseModel):
    device_type: str = Field(..., min_length=1)


class SetParentBody(BaseModel):
    parent_device_id: Optional[int] = None


@router.post("/{device_id}/apply-profile")
async def apply_profile(
    device_id: int,
    payload: ApplyProfileBody,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    try:
        result = await apply_profile_to_host(device_id, payload.device_type)
    except LicenseLimitError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.create_audit_log(
        "profile.apply",
        actor=user.username,
        resource="host",
        resource_id=str(device_id),
        detail=payload.device_type,
    )
    return result


@router.post("/{device_id}/parent")
async def set_parent(
    device_id: int,
    payload: SetParentBody,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    try:
        host = await db.set_device_parent(device_id, payload.parent_device_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not host:
        raise HTTPException(status_code=404, detail="Device not found")
    await db.create_audit_log(
        "topology.set_parent",
        actor=user.username,
        resource="host",
        resource_id=str(device_id),
        detail=str(payload.parent_device_id),
    )
    return host
