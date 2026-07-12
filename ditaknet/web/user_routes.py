"""Settings / Users & Roles admin pages."""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ditaknet import database as db
from ditaknet.config import settings
from ditaknet.core.rbac import public_permission_groups
from ditaknet.i18n import supported_languages, translate
from ditaknet.security import (
    AuthenticatedUser,
    hash_password,
    require_permissions,
    validate_password_strength,
    verify_web_csrf,
)
from ditaknet.web.routes import format_datetime, render_template

templates_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "templates"))
templates = Jinja2Templates(directory=templates_dir)
templates.env.filters["datetime"] = format_datetime
templates.env.globals["app_name"] = settings.app_name

router = APIRouter(include_in_schema=False, dependencies=[Depends(verify_web_csrf)])


def _active(value: Optional[str]) -> bool:
    return str(value or "").lower() in {"1", "true", "yes", "on"}


def _redirect(*, msg: str = "", error: str = "") -> RedirectResponse:
    suffix = ""
    if msg:
        suffix = f"?msg={msg}"
    elif error:
        suffix = f"?error={error}"
    return RedirectResponse(url=f"/settings/users{suffix}", status_code=303)


def _can_modify_target(actor: AuthenticatedUser, target: dict) -> bool:
    if target.get("is_superadmin") and not actor.is_superadmin:
        return False
    return True


@router.get("/settings/users", response_class=HTMLResponse)
async def users_settings_page(
    request: Request,
    msg: str = Query(""),
    error: str = Query(""),
    user: AuthenticatedUser = Depends(require_permissions("users.view")),
):
    return render_template(
        request,
        "settings/users.html",
        {
            "active": "users",
            "users": await db.list_users(),
            "roles": await db.list_roles(),
            "permission_groups": public_permission_groups(),
            "audit_logs": await db.list_audit_logs(limit=50, offset=0),
            "message": msg,
            "error": error,
            "current_user": user,
            "languages": supported_languages(),
            "t": lambda k, **kw: translate(k, request.session.get("lang", "en"), **kw),
        },
    )


@router.post("/settings/users/create")
async def users_create_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    telegram: str = Form(""),
    role: str = Form("viewer"),
    is_active: Optional[str] = Form("on"),
    is_superadmin: Optional[str] = Form(None),
    must_change_password: Optional[str] = Form("on"),
    permissions: list[str] = Form(default=[]),
    user: AuthenticatedUser = Depends(require_permissions("users.create")),
):
    if _active(is_superadmin) and not user.is_superadmin:
        return _redirect(error="superadmin_required")
    password_errors = validate_password_strength(password)
    if password_errors:
        return _redirect(error="weak_password")
    try:
        created = await db.create_user(
            username=username,
            password_hash=hash_password(password),
            full_name=full_name,
            email=email,
            phone=phone,
            telegram=telegram,
            role=role,
            is_active=_active(is_active),
            is_superadmin=_active(is_superadmin),
            must_change_password=_active(must_change_password),
            permissions=permissions,
        )
        await db.create_audit_log(
            "user.created",
            actor=user.username,
            resource="user",
            resource_id=created["id"],
            detail=f"role={created['role']}",
            ip_address=request.client.host if request.client else "",
        )
        return _redirect(msg="user_created")
    except Exception:
        return _redirect(error="create_failed")


@router.post("/settings/users/{user_id}/update")
async def users_update_post(
    user_id: int,
    request: Request,
    full_name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    telegram: str = Form(""),
    role: str = Form("viewer"),
    is_active: Optional[str] = Form(None),
    is_superadmin: Optional[str] = Form(None),
    must_change_password: Optional[str] = Form(None),
    permissions: list[str] = Form(default=[]),
    user: AuthenticatedUser = Depends(require_permissions("users.edit")),
):
    target = await db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if not _can_modify_target(user, target):
        return _redirect(error="superadmin_required")
    if user.id == user_id and not _active(is_active):
        return _redirect(error="cannot_disable_self")
    is_super = _active(is_superadmin) if user.is_superadmin else bool(target.get("is_superadmin"))
    await db.update_user(
        user_id,
        full_name=full_name,
        email=email,
        phone=phone,
        telegram=telegram,
        role="super_admin" if is_super else role,
        is_active=_active(is_active),
        is_superadmin=is_super,
        must_change_password=_active(must_change_password),
        permissions=permissions,
    )
    await db.create_audit_log(
        "user.updated",
        actor=user.username,
        resource="user",
        resource_id=user_id,
        detail="settings/users form",
        ip_address=request.client.host if request.client else "",
    )
    return _redirect(msg="user_updated")


@router.post("/settings/users/{user_id}/reset-password")
async def users_reset_password_post(
    user_id: int,
    request: Request,
    password: str = Form(...),
    must_change_password: Optional[str] = Form("on"),
    user: AuthenticatedUser = Depends(require_permissions("users.reset_password")),
):
    target = await db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if not _can_modify_target(user, target):
        return _redirect(error="superadmin_required")
    if validate_password_strength(password):
        return _redirect(error="weak_password")
    await db.update_user_password(
        user_id,
        hash_password(password),
        must_change_password=_active(must_change_password),
    )
    await db.create_audit_log(
        "user.password_reset",
        actor=user.username,
        resource="user",
        resource_id=user_id,
        ip_address=request.client.host if request.client else "",
    )
    return _redirect(msg="password_reset")


@router.post("/settings/users/{user_id}/disable")
async def users_disable_post(
    user_id: int,
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("users.disable")),
):
    target = await db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if not _can_modify_target(user, target):
        return _redirect(error="superadmin_required")
    if user.id == user_id:
        return _redirect(error="cannot_disable_self")
    await db.set_user_active(user_id, False)
    await db.create_audit_log(
        "user.disabled",
        actor=user.username,
        resource="user",
        resource_id=user_id,
        ip_address=request.client.host if request.client else "",
    )
    return _redirect(msg="user_disabled")


@router.post("/settings/users/{user_id}/revoke-sessions")
async def users_revoke_sessions_post(
    user_id: int,
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("users.edit")),
):
    target = await db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if not _can_modify_target(user, target):
        return _redirect(error="superadmin_required")
    await db.revoke_user_sessions(user_id)
    await db.create_audit_log(
        "user.sessions_revoked",
        actor=user.username,
        resource="user",
        resource_id=user_id,
        ip_address=request.client.host if request.client else "",
    )
    return _redirect(msg="sessions_revoked")

