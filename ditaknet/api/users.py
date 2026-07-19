"""User, role, and permission administration API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ditaknet import database as db
from ditaknet.core.rbac import public_permission_groups
from ditaknet.security import (
    AuthenticatedUser,
    hash_password,
    require_permissions,
    validate_password_strength,
)

router = APIRouter(prefix="/users", tags=["users"])


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    full_name: str = ""
    email: str = ""
    phone: str = ""
    telegram: str = ""
    role: str = "viewer"
    is_active: bool = True
    is_superadmin: bool = False
    must_change_password: bool = True
    permissions: list[str] = Field(default_factory=list)


class UserUpdateRequest(BaseModel):
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    telegram: str | None = None
    role: str | None = None
    is_active: bool | None = None
    is_superadmin: bool | None = None
    must_change_password: bool | None = None
    permissions: list[str] | None = None


class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    must_change_password: bool = True


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(user)
    cleaned.pop("password_hash", None)
    cleaned.pop("permissions_json", None)
    return cleaned


def _can_modify_target(actor: AuthenticatedUser, target: dict[str, Any]) -> bool:
    if bool(target.get("is_superadmin")) and not actor.is_superadmin:
        return False
    return True


@router.get("")
async def list_users(
    user: AuthenticatedUser = Depends(require_permissions("users.view")),
) -> dict:
    return {"users": [_public_user(row) for row in await db.list_users()]}


@router.get("/roles")
async def list_roles(
    user: AuthenticatedUser = Depends(require_permissions("users.view")),
) -> dict:
    return {"roles": await db.list_roles()}


@router.get("/permissions")
async def permissions_catalog(
    user: AuthenticatedUser = Depends(require_permissions("users.view")),
) -> dict:
    return {"permission_groups": public_permission_groups()}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreateRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("users.create")),
) -> dict:
    if payload.is_superadmin and not user.is_superadmin:
        raise HTTPException(status_code=403, detail="Only Super Admin can create Super Admin users")
    errors = validate_password_strength(payload.password)
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})
    try:
        created = await db.create_user(
            username=payload.username,
            password_hash=hash_password(payload.password),
            full_name=payload.full_name,
            email=payload.email,
            phone=payload.phone,
            telegram=payload.telegram,
            role=payload.role,
            is_active=payload.is_active,
            is_superadmin=payload.is_superadmin,
            must_change_password=payload.must_change_password,
            permissions=payload.permissions,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.create_audit_log(
        "user.created",
        actor=user.username,
        resource="user",
        resource_id=created["id"],
        detail=f"role={created['role']}",
        ip_address=request.client.host if request.client else "",
    )
    return {"user": _public_user(created)}


@router.patch("/{user_id}")
async def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("users.edit")),
) -> dict:
    target = await db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if not _can_modify_target(user, target):
        raise HTTPException(status_code=403, detail="Only Super Admin can modify Super Admin users")
    fields = payload.model_dump(exclude_unset=True)
    if fields.get("is_superadmin") and not user.is_superadmin:
        raise HTTPException(status_code=403, detail="Only Super Admin can grant Super Admin")
    if fields.get("is_active") is False and user.id == user_id:
        raise HTTPException(status_code=400, detail="You cannot disable your own account")
    updated = await db.update_user(user_id, **fields)
    await db.create_audit_log(
        "user.updated",
        actor=user.username,
        resource="user",
        resource_id=user_id,
        detail="fields=" + ",".join(sorted(fields.keys())),
        ip_address=request.client.host if request.client else "",
    )
    return {"user": _public_user(updated or target)}

@router.post("/{user_id}/disable")
async def disable_user(
    user_id: int,
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("users.disable")),
) -> dict:
    target = await db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if not _can_modify_target(user, target):
        raise HTTPException(status_code=403, detail="Only Super Admin can disable Super Admin users")
    if user.id == user_id:
        raise HTTPException(status_code=400, detail="You cannot disable your own account")
    updated = await db.set_user_active(user_id, False)
    await db.create_audit_log(
        "user.disabled",
        actor=user.username,
        resource="user",
        resource_id=user_id,
        ip_address=request.client.host if request.client else "",
    )
    return {"user": _public_user(updated or target)}


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: int,
    payload: PasswordResetRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("users.reset_password")),
) -> dict:
    target = await db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if not _can_modify_target(user, target):
        raise HTTPException(status_code=403, detail="Only Super Admin can reset Super Admin passwords")
    errors = validate_password_strength(payload.password)
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})
    updated = await db.update_user_password(
        user_id,
        hash_password(payload.password),
        must_change_password=payload.must_change_password,
    )
    await db.create_audit_log(
        "user.password_reset",
        actor=user.username,
        resource="user",
        resource_id=user_id,
        ip_address=request.client.host if request.client else "",
    )
    return {"user": _public_user(updated or target)}


@router.post("/{user_id}/revoke-sessions")
async def revoke_sessions(
    user_id: int,
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("users.edit")),
) -> dict:
    target = await db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if not _can_modify_target(user, target):
        raise HTTPException(status_code=403, detail="Only Super Admin can revoke Super Admin sessions")
    updated = await db.revoke_user_sessions(user_id)
    await db.create_audit_log(
        "user.sessions_revoked",
        actor=user.username,
        resource="user",
        resource_id=user_id,
        ip_address=request.client.host if request.client else "",
    )
    return {"user": _public_user(updated or target)}
