"""
Safe bulk operations — audited, permission-controlled, license-aware.

No remote command execution; only DitakNet configuration changes.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ditaknet import database as db
from ditaknet.api.deps import get_scheduler
from ditaknet.api.discovery import ImportRequest, import_devices
from ditaknet.core.licensing import LicenseLimitError, license_service
from ditaknet.core.profile_apply import apply_profile_to_host
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/bulk", tags=["bulk"])


class BulkIds(BaseModel):
    device_ids: list[int] = Field(..., min_length=1)


class BulkApplyProfile(BulkIds):
    device_type: str


class BulkAssignLocation(BulkIds):
    location: str = Field(..., min_length=1)


class BulkAssignTags(BulkIds):
    tags: str = Field(..., min_length=1)


class BulkImport(BulkIds):
    create_checks: bool = True


async def _audit_bulk(request: Request, user: AuthenticatedUser, action: str, detail: str) -> None:
    ip = request.client.host if request.client else ""
    await db.create_audit_log(action, actor=user.username, resource="bulk", detail=detail, ip_address=ip)


@router.post("/apply-profile")
async def bulk_apply_profile(
    payload: BulkApplyProfile,
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    try:
        await license_service.enforce_bulk_operation(len(payload.device_ids))
    except LicenseLimitError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    results = []
    for device_id in payload.device_ids:
        try:
            results.append(await apply_profile_to_host(device_id, payload.device_type))
        except LicenseLimitError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    await _audit_bulk(request, user, "bulk.apply_profile", payload.device_type)
    return {"applied": results}


async def _enforce_bulk(payload: BulkIds) -> None:
    await license_service.enforce_bulk_operation(len(payload.device_ids))


@router.post("/enable-monitoring")
async def bulk_enable(
    payload: BulkIds,
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    await _enforce_bulk(payload)
    count = await db.bulk_set_hosts_enabled(payload.device_ids, True)
    scheduler = get_scheduler()
    for hid in payload.device_ids:
        for svc in await db.list_services(host_id=hid):
            if svc.get("enabled") and hasattr(scheduler, "add_service"):
                scheduler.add_service(svc)
    await _audit_bulk(request, user, "bulk.enable", str(len(payload.device_ids)))
    return {"updated": count}


@router.post("/disable-monitoring")
async def bulk_disable(
    payload: BulkIds,
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    await _enforce_bulk(payload)
    count = await db.bulk_set_hosts_enabled(payload.device_ids, False)
    scheduler = get_scheduler()
    for hid in payload.device_ids:
        for svc in await db.list_services(host_id=hid):
            if hasattr(scheduler, "remove_service"):
                scheduler.remove_service(svc["id"])
    await _audit_bulk(request, user, "bulk.disable", str(len(payload.device_ids)))
    return {"updated": count}


@router.post("/run-checks")
async def bulk_run_checks(
    payload: BulkIds,
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    await _enforce_bulk(payload)
    from ditaknet.api.v1.services import _run_service_check_now

    ran = []
    for device_id in payload.device_ids:
        for svc in await db.list_services(host_id=device_id):
            try:
                await _run_service_check_now(svc["id"])
                ran.append(svc["id"])
            except HTTPException:
                pass
    await _audit_bulk(request, user, "bulk.run_checks", str(len(ran)))
    return {"services_checked": ran}


@router.post("/assign-location")
async def bulk_location(
    payload: BulkAssignLocation,
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    await _enforce_bulk(payload)
    count = await db.bulk_assign_location(payload.device_ids, payload.location)
    await _audit_bulk(request, user, "bulk.assign_location", payload.location)
    return {"updated": count}


@router.post("/assign-tags")
async def bulk_tags(
    payload: BulkAssignTags,
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    await _enforce_bulk(payload)
    count = await db.bulk_assign_tags(payload.device_ids, payload.tags)
    await _audit_bulk(request, user, "bulk.assign_tags", payload.tags)
    return {"updated": count}


@router.post("/import-discovered")
async def bulk_import_discovered(
    payload: BulkImport,
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    await _enforce_bulk(payload)
    result = await import_devices(
        ImportRequest(device_ids=payload.device_ids, create_checks=payload.create_checks),
        user,
    )
    await _audit_bulk(request, user, "bulk.import", str(len(payload.device_ids)))
    return result
