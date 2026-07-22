"""System, scheduler, audit, maintenance, version, and backup API endpoints."""

from __future__ import annotations

import platform
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from ditaknet import database as db
from ditaknet.api.deps import get_scheduler
from ditaknet.config import settings
from ditaknet.core.backup import create_backup
from ditaknet.core.system_about import build_about_payload
from ditaknet.core.build_metadata import build_metadata
from ditaknet.core.updates import (
    dismiss_update_version,
    get_update_status,
    set_check_enabled,
    snooze_update_banner,
)
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(tags=["system"])
system_router = APIRouter(prefix="/system", tags=["system"])


class UpdatePreferenceRequest(BaseModel):
    enabled: bool | None = None
    action: Literal["snooze", "dismiss"] | None = None
    version: str | None = None
    hours: int | None = Field(default=None, ge=1, le=168)


class UpdatePreflightRequest(BaseModel):
    target_version: str = Field(..., min_length=5, max_length=64)
    confirmation: str = Field(..., min_length=1, max_length=96)


async def _scheduler_payload() -> dict:
    try:
        scheduler = get_scheduler()
    except RuntimeError:
        return {
            "running": False,
            "job_count": 0,
            "jobs": [],
            "error": "not_initialised",
        }
    try:
        if hasattr(scheduler, "status"):
            return await scheduler.status()
        sched_obj = getattr(scheduler, "_scheduler", None)
        jobs = (
            sched_obj.get_jobs() if sched_obj and hasattr(sched_obj, "get_jobs") else []
        )
        return {
            "running": bool(getattr(sched_obj, "running", False)),
            "job_count": len(jobs),
            "jobs": [
                {"id": getattr(job, "id", ""), "name": getattr(job, "name", "")}
                for job in jobs
            ],
        }
    except Exception as exc:
        return {
            "running": False,
            "job_count": 0,
            "jobs": [],
            "error": type(exc).__name__,
        }


@router.get("/scheduler/status")
@system_router.get("/scheduler")
async def scheduler_status(
    user: AuthenticatedUser = Depends(require_permissions("read")),
) -> dict:
    """Return scheduler running state and active jobs."""
    return await _scheduler_payload()


@system_router.get("/version")
async def system_version() -> dict:
    meta = build_metadata(friendly_missing=True)
    update_status = await get_update_status()
    return {
        "app_name": settings.app_name,
        "version": meta["version"],
        "build_commit": meta["build_commit"],
        "build_date": meta["build_date"],
        "image_tag": meta["image_tag"],
        "update_channel": update_status.get("update_channel") or "stable",
        "github_repository": meta["github_repository"],
        "ghcr_image": update_status.get("ghcr_image"),
        "python_version": platform.python_version(),
        "update_status": update_status,
    }


@system_router.get("/update")
@system_router.get("/update-status")
async def system_update_status(
    user: AuthenticatedUser = Depends(require_permissions("read")),
) -> dict:
    """Return public-safe update status for Docker/TrueNAS deployments."""
    payload = await get_update_status(force=False)
    return {
        "current_version": payload.get("current_version"),
        "latest_version": payload.get("latest_version"),
        "update_available": bool(payload.get("update_available")),
        "critical": bool(payload.get("critical")),
        "last_checked_at": payload.get("last_checked_at"),
        "release_url": payload.get("release_url"),
        "changelog_url": payload.get("changelog_url"),
        "docker_image": payload.get("docker_image"),
        "pull_command": payload.get("pull_command"),
        "upgrade_hint": payload.get("upgrade_hint"),
        "localized_message": payload.get("localized_message"),
        "show_banner": bool(payload.get("show_banner")),
        "can_dismiss": bool(payload.get("can_dismiss")),
        "auto_update_enabled": False,
        "error": payload.get("error"),
        **payload,
    }


@system_router.post("/check-updates")
async def system_check_updates(
    user: AuthenticatedUser = Depends(require_permissions("admin")),
) -> dict:
    from ditaknet.core.notifications_service import (
        notify_customer_notices,
        notify_update_available,
        notify_update_check_failed,
    )

    payload = await get_update_status(force=True)
    if payload.get("update_available"):
        await notify_update_available(payload)
    await notify_customer_notices(payload)
    if payload.get("error") or payload.get("source") == "error":
        await notify_update_check_failed(payload)
    elif payload.get("source") == "disabled":
        pass
    elif not payload.get("source_configured"):
        await notify_update_check_failed(payload, not_configured=True)
    return {
        "current_version": payload.get("current_version"),
        "latest_version": payload.get("latest_version"),
        "update_available": bool(payload.get("update_available")),
        "critical": bool(payload.get("critical")),
        "last_checked_at": payload.get("last_checked_at"),
        "release_url": payload.get("release_url"),
        "error": payload.get("error"),
        **payload,
    }


@system_router.post("/update-preferences")
async def system_update_preferences(
    body: UpdatePreferenceRequest,
    user: AuthenticatedUser = Depends(require_permissions("admin")),
) -> dict:
    """Snooze / dismiss banner or enable/disable update checks (admin only)."""
    if body.enabled is not None:
        await set_check_enabled(bool(body.enabled))
    action = (body.action or "").strip().lower()
    if action == "snooze":
        return await snooze_update_banner(hours=int(body.hours or 24))
    if action == "dismiss":
        try:
            return await dismiss_update_version(body.version)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await get_update_status(force=False)


@system_router.get("/update-preflight")
async def system_last_update_preflight(
    user: AuthenticatedUser = Depends(require_permissions("admin")),
) -> dict:
    """Return the latest admin-only handoff receipt, if one exists."""
    from ditaknet.core.update_preflight import get_last_update_preflight

    return {"receipt": await get_last_update_preflight()}


@system_router.post("/update-preflight")
async def system_prepare_update(
    body: UpdatePreflightRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("admin")),
) -> dict:
    """Validate metadata/compatibility and create a mandatory recovery point.

    The endpoint never starts Docker or TrueNAS operations. It returns an
    auditable operator handoff only after the final backup artifact validates.
    """
    from ditaknet.core.update_preflight import UpdatePreflightError, prepare_update

    try:
        return await prepare_update(
            target_version=body.target_version,
            confirmation=body.confirmation,
            actor=user.username,
            ip_address=request.client.host if request.client else "",
        )
    except UpdatePreflightError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "backup_unavailable", "message": str(exc)},
        ) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "backup_validation_failed", "message": str(exc)},
        ) from exc


@system_router.get("/info")
async def system_info(
    user: AuthenticatedUser = Depends(require_permissions("read")),
) -> dict:
    return settings.safe_system_info()


@system_router.get("/about")
async def system_about() -> dict:
    """Public-safe product, license, and support metadata (no secrets)."""
    return await build_about_payload()


@system_router.post("/backup")
async def system_backup(
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("admin")),
) -> dict:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    try:
        backup = create_backup(
            payload.get("filename") if isinstance(payload, dict) else None
        )
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


@router.get("/maintenance")
@system_router.get("/maintenance")
async def get_maintenance(
    user: AuthenticatedUser = Depends(require_permissions("read")),
) -> dict:
    return {
        "maintenance_mode": await db.get_maintenance_mode(settings.maintenance_mode)
    }


@router.post("/maintenance")
@system_router.post("/maintenance")
async def set_maintenance(
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("admin")),
) -> dict:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    enabled = bool(payload.get("enabled", payload.get("maintenance_mode", False)))
    await db.set_maintenance_mode(enabled)
    try:
        await db.create_audit_log(
            "maintenance.update",
            actor=user.username,
            resource="maintenance",
            detail=f"enabled={enabled}",
            ip_address=request.client.host if request.client else "",
        )
    except Exception:
        pass
    return {"maintenance_mode": enabled}


@router.get("/audit")
@router.get("/audit-logs")
@system_router.get("/audit-logs")
async def audit_logs(
    limit: int = 100,
    offset: int = 0,
    user: AuthenticatedUser = Depends(require_permissions("admin")),
) -> dict:
    from ditaknet.core.licensing import LicenseLimitError, license_service

    try:
        await license_service.enforce_audit_logs_access()
    except LicenseLimitError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    safe_limit = min(max(limit, 1), 1000)
    safe_offset = max(offset, 0)
    return {
        "audit_logs": await db.list_audit_logs(limit=safe_limit, offset=safe_offset)
    }


class FactoryResetRequest(BaseModel):
    confirmation: str = Field(..., min_length=1)


@system_router.post("/reset/factory")
async def factory_reset_api(
    payload: FactoryResetRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("admin")),
) -> dict:
    from ditaknet.core.system_reset import factory_reset_to_setup

    try:
        await factory_reset_to_setup(
            actor=user.username,
            ip_address=request.client.host if request.client else "",
            confirmation=payload.confirmation,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"status": "ok", "setup_required": True, "redirect": "/setup"}
