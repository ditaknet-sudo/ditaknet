"""Troubleshooting assistant API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ditaknet import database as db
from ditaknet.assistant.troubleshooting import analyze_alert, analyze_device
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/assistant", tags=["assistant"])


@router.get("/device/{device_id}")
async def assistant_for_device(
    device_id: int,
    lang: str = Query("en"),
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    host = await db.get_host(device_id)
    if not host:
        raise HTTPException(status_code=404, detail="Device not found")
    services = await db.list_services(host_id=device_id)
    return await analyze_device(host, services, lang)


@router.get("/alert/{alert_id}")
async def assistant_for_alert(
    alert_id: int,
    lang: str = Query("en"),
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    alert = await db.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    service = await db.get_service(alert["service_id"])
    host = await db.get_host(service["host_id"]) if service else None
    all_services = await db.list_services(host_id=host["id"]) if host else []
    return await analyze_alert(alert, service, host, all_services, lang)
