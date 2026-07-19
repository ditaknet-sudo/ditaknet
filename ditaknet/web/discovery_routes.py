"""Discovery dashboard pages."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ditaknet.core.discovery_dashboard import build_discovery_page_context
from ditaknet.core.licensing import LicenseLimitError, license_service
from ditaknet.discovery import store as discovery_store
from ditaknet.discovery.networks_service import create_monitored_network, start_network_scan
from ditaknet.discovery.scheduler import discovery_scheduler
from ditaknet.i18n import translate
from ditaknet.resilience import get_request_id
from ditaknet.security import require_web_permissions, user_from_session, verify_web_csrf
from ditaknet.web.routes import render_template

router = APIRouter(include_in_schema=False, dependencies=[Depends(verify_web_csrf)])


def _ctx(request: Request, **extra):
    lang = request.session.get("lang", "en")
    base = {"lang": lang, "t": lambda k, **kw: translate(k, lang, **kw)}
    base.update(extra)
    return base


async def _presence_employees_for_linking() -> list[dict]:
    try:
        if not (await license_service.status()).get("employee_presence_enabled"):
            return []
        from ditaknet.core import employee_presence

        return await employee_presence.list_employees(status="active")
    except Exception:
        return []


@router.get("/discovery", response_class=HTMLResponse)
async def discovery_home(
    request: Request,
    tab: str = Query("networks"),
    scan_id: int | None = Query(None),
    user: str = Depends(require_web_permissions("discovery.view")),
):
    ctx = await build_discovery_page_context(selected_scan_id=scan_id, active_tab=tab)
    return render_template(
        request,
        "discovery/index.html",
        context=_ctx(request, **ctx, presence_employees=await _presence_employees_for_linking()),
    )


@router.post("/discovery/networks")
async def discovery_add_network(
    request: Request,
    name: str = Form(...),
    cidr: str = Form(...),
    vlan_id: str = Form(""),
    description: str = Form(""),
    scan_mode: str = Form("normal"),
    enabled: str = Form("true"),
    auto_refresh_enabled: str = Form("true"),
    user: str = Depends(require_web_permissions("discovery.manage_networks")),
):
    try:
        await create_monitored_network(
            name=name,
            cidr=cidr,
            vlan_id=vlan_id,
            description=description,
            scan_mode=scan_mode,
            enabled=enabled.lower() in {"1", "true", "on", "yes"},
            auto_refresh_enabled=auto_refresh_enabled.lower() in {"1", "true", "on", "yes"},
        )
    except (ValueError, LicenseLimitError) as exc:
        ctx = await build_discovery_page_context(active_tab="networks")
        return render_template(
            request,
            "discovery/index.html",
            context=_ctx(request, **ctx, error=str(exc)),
            status_code=400,
        )
    return RedirectResponse(url="/discovery?tab=networks", status_code=303)


@router.post("/discovery/networks/{network_id}/scan")
async def discovery_scan_network(
    network_id: int,
    request: Request,
    user: str = Depends(require_web_permissions("discovery.scan")),
):
    try:
        result = await start_network_scan(network_id, request_id=get_request_id(request))
        return RedirectResponse(url=f"/discovery?tab=results&scan_id={result['scan_id']}", status_code=303)
    except (ValueError, LicenseLimitError) as exc:
        ctx = await build_discovery_page_context(active_tab="networks")
        return render_template(
            request,
            "discovery/index.html",
            context=_ctx(request, **ctx, error=str(exc)),
            status_code=400,
        )


@router.post("/discovery/networks/{network_id}/delete")
async def discovery_delete_network(
    network_id: int,
    request: Request,
    user: str = Depends(require_web_permissions("discovery.manage_networks")),
):
    await discovery_store.delete_monitored_network(network_id)
    await discovery_store.purge_unauthorized_discovery_records()
    return RedirectResponse(url="/discovery?tab=networks", status_code=303)


@router.post("/discovery/cleanup-demo")
async def discovery_cleanup_demo(
    request: Request,
    user: str = Depends(require_web_permissions("settings.edit")),
):
    await discovery_store.cleanup_demo_discovery_data()
    await discovery_store.purge_unauthorized_discovery_records()
    return RedirectResponse(url="/discovery?tab=settings", status_code=303)


@router.post("/discovery/settings")
async def discovery_save_settings(
    request: Request,
    auto_refresh_enabled: str = Form("true"),
    refresh_interval_minutes: int = Form(10),
    stale_after_minutes: int = Form(30),
    offline_after_minutes: int = Form(60),
    scan_mode: str = Form("normal"),
    user: str = Depends(require_web_permissions("settings.edit")),
):
    await discovery_store.update_discovery_settings(
        auto_refresh_enabled=auto_refresh_enabled.lower() in {"1", "true", "on", "yes"},
        refresh_interval_minutes=refresh_interval_minutes,
        stale_after_minutes=stale_after_minutes,
        offline_after_minutes=offline_after_minutes,
        scan_mode=scan_mode,
    )
    return RedirectResponse(url="/discovery?tab=settings", status_code=303)


@router.get("/discovery/new", response_class=HTMLResponse)
async def discovery_new(request: Request, user: str = Depends(require_web_permissions("discovery.view"))):
    return RedirectResponse(url="/discovery?tab=networks", status_code=303)


@router.get("/discovery/scans", response_class=HTMLResponse)
async def discovery_scans(request: Request, user: str = Depends(require_web_permissions("discovery.view"))):
    return RedirectResponse(url="/discovery?tab=history", status_code=303)


@router.get("/discovery/scans/{scan_id}", response_class=HTMLResponse)
async def discovery_scan_detail(
    scan_id: int,
    request: Request,
    user: str = Depends(require_web_permissions("discovery.view")),
):
    return RedirectResponse(url=f"/discovery?tab=results&scan_id={scan_id}", status_code=303)


@router.post("/discovery/scans/{scan_id}/cancel")
async def discovery_scan_cancel(
    scan_id: int,
    request: Request,
    user: str = Depends(require_web_permissions("discovery.scan")),
):
    await discovery_scheduler.cancel_scan(scan_id)
    return RedirectResponse(url=f"/discovery?tab=results&scan_id={scan_id}", status_code=303)


@router.get("/discovery/devices", response_class=HTMLResponse)
async def discovery_devices(
    request: Request,
    scan_id: int | None = Query(None),
    user: str = Depends(require_web_permissions("discovery.view")),
):
    tab = "results" if scan_id else "new"
    url = f"/discovery?tab={tab}"
    if scan_id:
        url += f"&scan_id={scan_id}"
    return RedirectResponse(url=url, status_code=303)


@router.post("/discovery/devices/{device_id}/link-employee")
async def discovery_link_employee(
    device_id: int,
    request: Request,
    employee_id: int = Form(...),
    device_type: str = Form("laptop"),
    user: str = Depends(require_web_permissions("employees.edit")),
):
    try:
        from ditaknet.core import employee_presence

        await employee_presence.link_discovered_device_to_employee(
            employee_id=employee_id,
            discovered_device_id=device_id,
            device_type=device_type,
            actor=user,
        )
    except Exception:
        pass
    return RedirectResponse(url=request.headers.get("referer", "/discovery"), status_code=303)


@router.get("/discovery/import", response_class=HTMLResponse)
async def discovery_import_get(
    request: Request,
    scan_id: int | None = Query(None),
    user: str = Depends(require_web_permissions("discovery.view")),
):
    url = "/discovery?tab=results"
    if scan_id:
        url += f"&scan_id={scan_id}"
    return RedirectResponse(url=url, status_code=303)


@router.post("/discovery/devices/{device_id}/ignore")
async def discovery_ignore_device(
    device_id: int,
    request: Request,
    user: str = Depends(require_web_permissions("discovery.import")),
):
    await discovery_store.ignore_discovery_inventory_device(device_id)
    return RedirectResponse(url="/discovery?tab=new", status_code=303)


@router.post("/discovery/import")
async def discovery_import_post(
    request: Request,
    device_ids: list[int] = Form(default=[]),
    user: str = Depends(require_web_permissions("discovery.import")),
):
    from ditaknet.api.discovery import ImportRequest, import_devices

    session_user = user_from_session(request)
    if session_user is None:
        return RedirectResponse(url="/login", status_code=303)
    await import_devices(ImportRequest(device_ids=device_ids), session_user)
    return RedirectResponse(url="/hosts", status_code=303)
