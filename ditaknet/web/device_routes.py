"""Device detail web pages."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ditaknet import database as db
from ditaknet.api.deps import get_scheduler
from ditaknet.core.device_ids import format_device_id, parse_device_id
from ditaknet.core.device_monitoring import build_device_overview
from ditaknet.i18n import translate
from ditaknet.security import require_web_permissions, verify_web_csrf
from ditaknet.web.routes import render_template

router = APIRouter(include_in_schema=False, dependencies=[Depends(verify_web_csrf)])


def _device_i18n(lang: str) -> dict[str, str]:
    keys = [
        "device_overview",
        "uptime_24h",
        "uptime_7d",
        "uptime_30d",
        "response_time",
        "avg_response",
        "last_seen",
        "last_check",
        "run_check_now",
        "pause_monitoring",
        "monitoring_history",
        "no_monitoring_history",
        "recent_events",
        "service_checks",
        "device_up",
        "device_down",
        "device_unstable",
        "incident_count",
        "last_downtime",
        "recovered_at",
        "device_detail.current_response",
        "device_detail.check_interval",
        "device_detail.total_downtime",
        "device_detail.packet_loss",
        "device_detail.status_history",
        "device_detail.edit",
        "device_detail.delete",
        "device_detail.loading",
    ]
    return {key: translate(key, lang) for key in keys}


@router.get("/devices/new")
async def device_new_redirect(request: Request):
    """Redirect /devices/new to /hosts/new — prevent catch-all conflict."""
    return RedirectResponse(url="/hosts/new", status_code=303)


@router.get("/devices/{device_id}", response_class=HTMLResponse)
async def device_detail_page(
    request: Request,
    device_id: str,
    user: str = Depends(require_web_permissions("devices.view")),
):
    try:
        source, numeric_id = parse_device_id(device_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if source != "host":
        raise HTTPException(status_code=404, detail="Device detail is only available for monitored hosts")
    host = await db.get_host(numeric_id)
    if not host:
        raise HTTPException(status_code=404, detail="Device not found")
    lang = request.session.get("lang", "en")
    canonical_id = format_device_id("host", numeric_id)
    overview = await build_device_overview(numeric_id)
    return render_template(
        request,
        "devices/detail.html",
        {
            "host": host,
            "device_id": canonical_id,
            "overview": overview,
            "device_i18n": _device_i18n(lang),
        },
    )


@router.post("/devices/{device_id}/run-check")
async def device_run_check_post(
    request: Request,
    device_id: str,
    user: str = Depends(require_web_permissions("devices.run_check")),
):
    source, numeric_id = parse_device_id(device_id)
    if source != "host":
        raise HTTPException(status_code=400, detail="Unsupported device type")
    scheduler = get_scheduler()
    for svc in await db.list_services(numeric_id):
        if svc.get("enabled"):
            await scheduler.trigger_check(svc["id"])
    await db.create_audit_log("device.run_check", actor=user, resource="host", resource_id=str(numeric_id))
    return RedirectResponse(url=f"/devices/{format_device_id('host', numeric_id)}", status_code=303)


@router.post("/devices/{device_id}/pause")
async def device_pause_post(
    request: Request,
    device_id: str,
    user: str = Depends(require_web_permissions("devices.edit")),
):
    source, numeric_id = parse_device_id(device_id)
    if source != "host":
        raise HTTPException(status_code=400, detail="Unsupported device type")
    host = await db.update_host(numeric_id, enabled=False)
    if not host:
        raise HTTPException(status_code=404, detail="Device not found")
    scheduler = get_scheduler()
    for svc in await db.list_services(numeric_id):
        updated = await db.update_service(svc["id"], enabled=False)
        if updated:
            scheduler.reschedule_service(updated)
    return RedirectResponse(url=f"/devices/{format_device_id('host', numeric_id)}", status_code=303)


@router.post("/devices/{device_id}/delete")
async def device_delete_post(
    request: Request,
    device_id: str,
    user: str = Depends(require_web_permissions("devices.delete")),
):
    source, numeric_id = parse_device_id(device_id)
    if source != "host":
        raise HTTPException(status_code=400, detail="Unsupported device type")
    scheduler = get_scheduler()
    for svc in await db.list_services(numeric_id):
        scheduler.remove_service(svc["id"])
    await db.delete_host(numeric_id)
    return RedirectResponse(url="/devices", status_code=303)
