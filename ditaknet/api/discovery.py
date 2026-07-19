"""Discovery and license REST API."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ditaknet import database as db
from ditaknet.api.deps import get_scheduler
from ditaknet.core.licensing import LicenseLimitError, license_service
from ditaknet.discovery import store as discovery_store
from ditaknet.discovery.networks_service import (
    create_monitored_network,
    start_network_scan,
    update_monitored_network,
)
from ditaknet.discovery.diagnostics import scan_result_payload
from ditaknet.discovery.scheduler import discovery_scheduler
from ditaknet.discovery.scan_state import merge_progress
from ditaknet.discovery.auto_import import import_discovered_device
from ditaknet.discovery.subnet import detect_local_subnets, normalize_subnets
from ditaknet.resilience import get_request_id
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/discovery", tags=["discovery"])


class ScanCreate(BaseModel):
    subnets: list[str] = Field(default_factory=list)
    profile: str = Field(default="normal", pattern="^(quick|normal|deep)$")
    detect_local: bool = False
    monitored_network_id: int | None = None


class ImportRequest(BaseModel):
    device_ids: list[int] = Field(..., min_length=1)
    create_checks: bool = True


class MonitoredNetworkCreate(BaseModel):
    name: str
    cidr: str
    vlan_id: str = ""
    description: str = ""
    scan_mode: str = Field(default="normal", pattern="^(quick|normal|deep)$")
    enabled: bool = True
    auto_refresh_enabled: bool = True


class MonitoredNetworkUpdate(BaseModel):
    name: str | None = None
    cidr: str | None = None
    vlan_id: str | None = None
    description: str | None = None
    scan_mode: str | None = Field(default=None, pattern="^(quick|normal|deep)$")
    enabled: bool | None = None
    auto_refresh_enabled: bool | None = None


class DiscoverySettingsUpdate(BaseModel):
    auto_refresh_enabled: bool | None = None
    auto_import_enabled: bool | None = None
    auto_import_skip_mobile: bool | None = None
    refresh_interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    stale_after_minutes: int | None = Field(default=None, ge=1, le=10080)
    offline_after_minutes: int | None = Field(default=None, ge=1, le=10080)
    scan_mode: str | None = Field(default=None, pattern="^(quick|normal|deep)$")


@router.get("/networks")
async def list_networks(
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    return await discovery_store.list_monitored_networks()


@router.post("/networks", status_code=201)
async def create_network(
    payload: MonitoredNetworkCreate,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    try:
        return await create_monitored_network(**payload.model_dump())
    except (ValueError, LicenseLimitError) as exc:
        raise HTTPException(status_code=400 if isinstance(exc, ValueError) else 403, detail=str(exc)) from exc


@router.get("/networks/{network_id}")
async def get_network(
    network_id: int,
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    net = await discovery_store.get_monitored_network(network_id)
    if not net:
        raise HTTPException(status_code=404, detail="Monitored network not found")
    return net


@router.put("/networks/{network_id}")
async def put_network(
    network_id: int,
    payload: MonitoredNetworkUpdate,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    try:
        data = {k: v for k, v in payload.model_dump().items() if v is not None}
        return await update_monitored_network(network_id, **data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/networks/{network_id}")
async def delete_network(
    network_id: int,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    if not await discovery_store.delete_monitored_network(network_id):
        raise HTTPException(status_code=404, detail="Monitored network not found")
    return {"deleted": True}


@router.post("/networks/{network_id}/scan", status_code=201)
async def scan_network(
    network_id: int,
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    try:
        return await start_network_scan(network_id, request_id=get_request_id(request))
    except (ValueError, LicenseLimitError) as exc:
        raise HTTPException(status_code=400 if isinstance(exc, ValueError) else 403, detail=str(exc)) from exc


@router.post("/networks/{network_id}/refresh-now", status_code=201)
async def refresh_network_now(
    network_id: int,
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    try:
        return await start_network_scan(network_id, request_id=get_request_id(request))
    except (ValueError, LicenseLimitError) as exc:
        raise HTTPException(status_code=400 if isinstance(exc, ValueError) else 403, detail=str(exc)) from exc


@router.get("/subnets")
async def list_subnets(
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    local = detect_local_subnets()
    return {"local_subnets": local, "examples": ["192.168.1.0/24", "10.0.0.0/24"]}


@router.post("/scans", status_code=201)
async def create_scan(
    payload: ScanCreate,
    request: Request,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    if payload.monitored_network_id:
        try:
            return await start_network_scan(
                payload.monitored_network_id,
                request_id=get_request_id(request),
            )
        except (ValueError, LicenseLimitError) as exc:
            raise HTTPException(
                status_code=400 if isinstance(exc, ValueError) else 403,
                detail=str(exc),
            ) from exc
    subnets = list(payload.subnets)
    if payload.detect_local and not subnets:
        subnets = detect_local_subnets()
    try:
        normalized = normalize_subnets(subnets) if subnets else []
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not normalized:
        raise HTTPException(status_code=400, detail="At least one private subnet is required")
    monitored = await discovery_store.list_monitored_networks(enabled_only=True)
    if monitored:
        allowed = {str(n.get("cidr") or "") for n in monitored}
        if not all(cidr in allowed for cidr in normalized):
            raise HTTPException(
                status_code=400,
                detail="Scan only configured monitored subnets. Add the subnet under Monitored Networks first.",
            )
    try:
        await license_service.enforce_discovery_scan(normalized)
    except LicenseLimitError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    scan = await db.create_discovery_scan(
        payload.profile,
        json.dumps(normalized),
        request_id=get_request_id(request),
    )
    await discovery_scheduler.start_scan(scan["id"], normalized, payload.profile)
    return scan


@router.get("/scans")
async def list_scans(
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    rows = []
    for scan in await db.list_discovery_scans():
        progress = discovery_scheduler.get_progress(int(scan["id"]))
        payload = scan_result_payload(scan, devices=[], live_progress=progress)
        payload.pop("devices", None)
        rows.append({**scan, **payload, "id": scan["id"]})
    return rows


@router.get("/scans/{scan_id}")
async def get_scan(
    scan_id: int,
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    scan = await db.get_discovery_scan(scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")
    progress = discovery_scheduler.get_progress(scan_id)
    devices = await db.list_discovered_devices(scan_id=scan_id, hide_demo=True)
    payload = scan_result_payload(scan, devices=devices, live_progress=progress)
    return {
        **payload,
        "id": scan_id,
        "scan": scan,
        "progress": merge_progress(scan, progress),
        "devices": devices,
    }


@router.get("/scans/{scan_id}/devices")
async def get_scan_devices(
    scan_id: int,
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    scan = await db.get_discovery_scan(scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")
    return await db.list_discovered_devices(scan_id=scan_id, hide_demo=True)


@router.post("/scans/{scan_id}/cancel")
async def cancel_scan(
    scan_id: int,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    scan = await db.get_discovery_scan(scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")
    await discovery_scheduler.cancel_scan(scan_id)
    return {"cancelled": True}


@router.get("/scans/{scan_id}/events")
async def get_scan_events(
    scan_id: int,
    limit: int = 500,
    offset: int = 0,
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    scan = await db.get_discovery_scan(scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail="Scan not found")
    events = await db.get_discovery_scan_events(scan_id, limit=limit, offset=offset)
    return {"events": events}


@router.get("/devices")
async def list_devices(
    scan_id: Optional[int] = None,
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    if scan_id is not None:
        return await db.list_discovered_devices(scan_id=scan_id, hide_demo=True)
    nets = await discovery_store.list_monitored_networks(enabled_only=True)
    if not nets:
        return []
    return await discovery_store.list_discovery_inventory(
        subnets=[str(n.get("cidr") or "") for n in nets if n.get("cidr")],
        limit=500,
        hide_demo=True,
    )


@router.post("/devices/{device_id}/import")
async def import_single_device(
    device_id: int,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    return await import_devices(ImportRequest(device_ids=[device_id]), user)


@router.post("/devices/bulk-import")
async def bulk_import_devices(
    payload: ImportRequest,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    return await import_devices(payload, user)


@router.post("/devices/{device_id}/ignore")
async def ignore_device(
    device_id: int,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    item = await discovery_store.ignore_discovery_inventory_device(device_id)
    if not item:
        raise HTTPException(status_code=404, detail="Discovery device not found")
    return item


@router.get("/changes")
async def list_changes(
    limit: int = 50,
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    return await discovery_store.list_discovery_change_events(limit=limit)


@router.get("/settings")
async def get_settings(
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    return await discovery_store.get_discovery_settings()


@router.put("/settings")
async def put_settings(
    payload: DiscoverySettingsUpdate,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    data = {k: v for k, v in payload.model_dump().items() if v is not None}
    return await discovery_store.update_discovery_settings(**data)


@router.post("/cleanup-demo")
async def cleanup_demo_discovery(
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    return await discovery_store.cleanup_demo_discovery_data()


@router.post("/purge-unauthorized")
async def purge_unauthorized(
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    return await discovery_store.purge_unauthorized_discovery_records()


@router.post("/import")
async def import_devices(
    payload: ImportRequest,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    imported = []
    scheduler = get_scheduler()
    for device_id in payload.device_ids:
        device = await db.get_discovered_device(device_id)
        if not device:
            inv_rows = await discovery_store.list_discovery_inventory(limit=1000)
            inv = next((i for i in inv_rows if int(i.get("id") or 0) == device_id), None)
            if inv:
                scan_id = int(inv.get("last_scan_id") or 0)
                if scan_id:
                    scan_devices = await db.list_discovered_devices(scan_id=scan_id, hide_demo=True)
                    device = next(
                        (d for d in scan_devices if d.get("ip_address") == inv.get("ip_address")),
                        None,
                    )
        if not device:
            continue
        try:
            result = await import_discovered_device(
                device,
                create_checks=payload.create_checks,
                scheduler=scheduler,
                actor=user.username,
            )
        except LicenseLimitError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        imported.append(result)
    return {"imported": imported}
