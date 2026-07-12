"""Device detail monitoring APIs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ditaknet.api.deps import get_scheduler
from ditaknet.core.device_ids import format_device_id, parse_device_id
from ditaknet.core.device_monitoring import (
    build_checks_history,
    build_device_overview,
    build_metrics_payload,
    build_recent_events,
    build_service_checks,
    build_uptime_payload,
)
from ditaknet import database as db
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/devices", tags=["device-detail"])


async def _resolve_host(device_id: str) -> int:
    source, numeric_id = parse_device_id(device_id)
    if source != "host":
        raise HTTPException(status_code=400, detail="Agent device detail is not supported yet")
    host = await db.get_host(numeric_id)
    if not host:
        raise HTTPException(status_code=404, detail="Device not found")
    return numeric_id


@router.get("/{device_id}/overview")
async def device_overview(
    device_id: str,
    user: AuthenticatedUser = Depends(require_permissions("read")),
) -> dict:
    host_id = await _resolve_host(device_id)
    overview = await build_device_overview(host_id)
    overview["checks"] = await build_service_checks(host_id)
    overview["recent_events"] = await build_recent_events(host_id)
    return overview


@router.get("/{device_id}/checks/history")
async def device_checks_history(
    device_id: str,
    limit: int = 200,
    user: AuthenticatedUser = Depends(require_permissions("read")),
) -> dict:
    host_id = await _resolve_host(device_id)
    return await build_checks_history(host_id, limit=limit)


@router.get("/{device_id}/uptime")
async def device_uptime(
    device_id: str,
    user: AuthenticatedUser = Depends(require_permissions("read")),
) -> dict:
    host_id = await _resolve_host(device_id)
    return await build_uptime_payload(host_id)


@router.get("/{device_id}/metrics")
async def device_metrics(
    device_id: str,
    user: AuthenticatedUser = Depends(require_permissions("read")),
) -> dict:
    host_id = await _resolve_host(device_id)
    return await build_metrics_payload(host_id)


@router.post("/{device_id}/run-check")
async def device_run_check(
    device_id: str,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
) -> dict:
    host_id = await _resolve_host(device_id)
    scheduler = get_scheduler()
    ran: list[int] = []
    for svc in await db.list_services(host_id):
        if svc.get("enabled"):
            await scheduler.trigger_check(svc["id"])
            ran.append(int(svc["id"]))
    await db.create_audit_log(
        "device.run_check",
        actor=user.username,
        resource="host",
        resource_id=str(host_id),
        detail=f"services={','.join(str(s) for s in ran)}",
    )
    overview = await build_device_overview(host_id)
    return {"ok": True, "device_id": format_device_id("host", host_id), "ran_service_ids": ran, "overview": overview}
