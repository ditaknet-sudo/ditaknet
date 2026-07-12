"""DitakNet web dashboard routes."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from loguru import logger

from markupsafe import Markup

from ditaknet import database as db
from ditaknet.api.deps import get_alert_engine, get_scheduler
from ditaknet.config import settings
from ditaknet.core.features import web_navigation
from ditaknet.i18n import supported_languages, translate
from ditaknet.security import (
    authenticate_user,
    ensure_csrf_token,
    require_web_permissions,
    session_role_from_request,
    validate_csrf_token,
    verify_web_csrf,
)
from ditaknet.web.forms import (
    host_form_as_dict,
    parse_checkbox,
    parse_optional_int,
    service_form_as_dict,
    validate_host_form,
    validate_service_form,
)

templates_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "templates"))
templates = Jinja2Templates(directory=templates_dir)

router = APIRouter(include_in_schema=False, dependencies=[Depends(verify_web_csrf)])

STATIC_ASSET_BUILD = "20260712darkfix"

def format_datetime(value):
    if not value:
        return "Never"
    try:
        dt = datetime.fromisoformat(str(value))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value


def status_badge_class(state: str) -> str:
    mapping = {
        "ok": "success",
        "up": "success",
        "warning": "warning",
        "critical": "danger",
        "down": "danger",
        "unknown": "secondary",
        "pending": "secondary",
        "disabled": "secondary",
        "offline": "danger",
        "onsite": "success",
        "remote": "primary",
        "away": "warning",
        "online": "success",
        "recovery": "success",
    }
    return mapping.get(str(state).lower(), "secondary")


templates.env.filters["datetime"] = format_datetime
templates.env.filters["status_badge"] = status_badge_class
templates.env.globals["app_name"] = settings.app_name
templates.env.globals["app_display_name"] = settings.app_display_name
templates.env.globals["author_name"] = settings.app_author_name
templates.env.globals["author_website"] = settings.app_author_website


def render_status_badge(state: str) -> Markup:
    css = status_badge_class(state)
    label = str(state or "unknown").upper()
    return Markup(f'<span class="badge bg-{css}">{label}</span>')


templates.env.globals["status_badge"] = render_status_badge


def device_type_icon(device_type: str) -> str:
    mapping = {
        "server": "bi-server",
        "router": "bi-router",
        "switch": "bi-hdd-network",
        "firewall": "bi-shield-lock",
        "storage": "bi-device-hdd",
        "endpoint": "bi-pc-display",
        "workstation": "bi-pc-display",
        "printer": "bi-printer",
        "camera": "bi-camera-video",
        "nvr": "bi-camera-reels",
        "dvr": "bi-camera-reels",
        "pc": "bi-pc-display",
        "mac": "bi-apple",
        "mobile_phone": "bi-phone",
        "access_point": "bi-wifi",
        "nas": "bi-device-hdd",
        "linux_server": "bi-server",
        "windows_server": "bi-windows",
        "unknown": "bi-diagram-3",
        "agent": "bi-cpu",
    }
    return mapping.get(str(device_type or "").lower(), "bi-diagram-3")


templates.env.globals["device_type_icon"] = device_type_icon


def _page_context(request: Request) -> dict:
    lang = request.session.get("lang", "en")
    nav_status_keys = [
        "nav_status_update_available",
        "nav_status_backup_overdue",
        "nav_status_domain_misconfigured",
        "nav_status_security_warning",
        "nav_status_license_limit_exceeded",
        "nav_status_license_expired",
        "nav_status_subnet_limit_exceeded",
        "nav_status_no_monitored_network",
        "nav_status_pending_imports",
        "nav_status_scan_failed",
        "nav_status_demo_data_detected",
        "nav_status_scheduler_stopped",
        "nav_status_database_issue",
        "nav_status_resource_pressure",
        "nav_status_storage_not_writable",
        "nav_status_notification_failed",
        "nav_status_unread_notifications",
    ]
    return {
        "lang": lang,
        "t": lambda k, **kw: translate(k, lang, **kw),
        "brand_display": translate("brand_name", lang),
        "app_name": settings.app_name,
        "app_display_name": settings.app_display_name,
        "app_version": settings.app_version,
        "static_asset_build": STATIC_ASSET_BUILD,
        "languages": supported_languages(),
        "nav_status_i18n": {key: translate(key, lang) for key in nav_status_keys},
        "csrf_token": ensure_csrf_token(request),
        "login_csrf": ensure_csrf_token(request),
    }


def render_template(
    request: Request,
    name: str,
    context: Optional[dict] = None,
    *,
    status_code: int = 200,
):
    payload = {"request": request, **_page_context(request)}
    if context:
        payload.update(context)
    license_status = None
    if isinstance(payload.get("license"), dict):
        license_status = payload["license"]
    elif isinstance(payload.get("stats"), dict) and isinstance(payload["stats"].get("license"), dict):
        license_status = payload["stats"]["license"]
    payload.update(web_navigation(license_status, session_role_from_request(request)))
    return templates.TemplateResponse(
        request,
        name,
        payload,
        status_code=status_code,
    )


async def get_current_user(request: Request) -> str:
    """Authenticated session user with dashboard visibility."""
    return await require_web_permissions("dashboard.view")(request)


def _enrich_checks(checks: list[dict], services: dict, hosts: dict) -> None:
    for item in checks:
        svc = services.get(item["service_id"], {})
        item["service_name"] = svc.get("name", "Unknown")
        item["host_name"] = hosts.get(svc.get("host_id", 0), {}).get("name", "Unknown")


def _enrich_alerts(alerts: list[dict], services: dict, hosts: dict) -> None:
    for item in alerts:
        svc = services.get(item["service_id"], {})
        item["service_name"] = svc.get("name", "Unknown")
        item["host_name"] = hosts.get(svc.get("host_id", 0), {}).get("name", "Unknown")
        item["is_active"] = item.get("resolved_at") is None


@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request, lang: str | None = None):
    if lang and lang in supported_languages():
        request.session["lang"] = lang
    return render_template(request, "login.html", {"login_csrf": ensure_csrf_token(request)})


@router.post("/login")
async def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    login_csrf: str = Form(""),
):
    validate_csrf_token(request, login_csrf)
    user = await authenticate_user(username, password)
    if user:
        request.session.clear()
        request.session["user"] = {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "is_active": user.is_active,
            "is_superadmin": user.is_superadmin,
            "explicit_permissions": sorted(user.explicit_permissions),
            "permissions": sorted(user.permissions),
            "session_version": user.session_version,
            "must_change_password": user.must_change_password,
        }
        request.session["role"] = user.role
        request.session["session_version"] = user.session_version
        request.session["is_superadmin"] = user.is_superadmin
        try:
            await db.create_audit_log(
                "login.success",
                actor=user.username,
                resource="session",
                ip_address=request.client.host if request.client else "",
            )
        except Exception as exc:
            logger.warning("Failed to create login audit log: {}", exc)
        return RedirectResponse(url="/dashboard", status_code=303)
    try:
        await db.create_audit_log(
            "login.failure",
            actor=username or "unknown",
            resource="session",
            ip_address=request.client.host if request.client else "",
        )
    except Exception as exc:
        logger.warning("Failed to create failed-login audit log: {}", exc)
    return render_template(
        request,
        "login.html",
        {"error": "Invalid username or password", "login_csrf": ensure_csrf_token(request)},
        status_code=401,
    )


@router.get("/logout")
async def logout(request: Request):
    session_user = request.session.get("user") or "unknown"
    actor = session_user.get("username", "unknown") if isinstance(session_user, dict) else session_user
    request.session.clear()
    try:
        await db.create_audit_log(
            "logout",
            actor=str(actor),
            resource="session",
            ip_address=request.client.host if request.client else "",
        )
    except Exception as exc:
        logger.warning("Failed to create logout audit log: {}", exc)
    return RedirectResponse(url="/login", status_code=303)


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user: str = Depends(get_current_user)):
    from ditaknet.assistant.recommendations import suggested_dashboard_actions
    from ditaknet.core.dashboard_overview import build_dashboard_overview
    from ditaknet.i18n import translate

    lang = request.session.get("lang", "en")
    overview = await build_dashboard_overview(lang)
    stats = overview["stats"]
    stats["license"] = overview["license"]

    if overview.get("show_corporate_widgets"):
        try:
            from ditaknet.core.hr.summary import attendance_dashboard_summary

            stats["hr_attendance"] = await attendance_dashboard_summary()
        except Exception as exc:
            logger.warning("HR attendance dashboard summary failed: {}", exc)
        try:
            from ditaknet.core.employee_presence import summarize_presence

            stats["employee_presence"] = await summarize_presence()
        except Exception as exc:
            logger.warning("Employee presence dashboard summary failed: {}", exc)
        if overview["license"].get("multi_office_enabled"):
            try:
                from ditaknet.core.hr.offices import offices_dashboard_summary

                stats["offices"] = await offices_dashboard_summary()
            except Exception as exc:
                logger.warning("Offices dashboard summary failed: {}", exc)

    suggested = await suggested_dashboard_actions(lang)
    return render_template(
        request,
        "dashboard.html",
        {
            "overview": overview,
            "stats": stats,
            "suggested_actions": suggested,
            "overview_asset_build": "20260711kuma",
            "t": lambda k, **kw: translate(k, lang, **kw),
            "refresh_seconds": 45,
        },
    )


@router.get("/devices", response_class=HTMLResponse)
async def devices_view(
    request: Request,
    state: str = Query("all"),
    source: str = Query("all"),
    user: str = Depends(require_web_permissions("devices.view")),
):
    from ditaknet.core.device_inventory_groups import group_inventory_devices
    from ditaknet.discovery import store as discovery_store
    from ditaknet.discovery.name_sync import refresh_host_names_from_discovery

    await refresh_host_names_from_discovery()

    inventory = await db.get_device_inventory()
    devices = list(inventory["devices"])
    summary = dict(inventory["summary"])
    discovery_settings = await discovery_store.get_discovery_settings()
    auto_import_enabled = discovery_settings.get("auto_import_enabled", True)
    pending_discovery_imports = await db.count_pending_discovery_imports()

    inventory_meta = {
        str(row.get("ip_address") or "").strip(): row
        for row in await discovery_store.list_discovery_inventory(limit=1000, hide_demo=True)
        if str(row.get("ip_address") or "").strip()
    }
    for device in devices:
        inv = inventory_meta.get(str(device.get("address") or "").strip())
        if inv:
            device["vendor"] = str(inv.get("vendor") or "")
            if not device.get("hostname"):
                device["hostname"] = str(inv.get("hostname") or "")
            if str(device.get("device_type") or "unknown") in {"unknown", "server"} and inv.get("detected_type"):
                device["device_type"] = str(inv.get("detected_type") or device.get("device_type"))

    include_discovered = (not auto_import_enabled) and source in {"all", "discovered"}
    if include_discovered:
        pending_rows = await db.list_pending_discovered_inventory()
        discovered_devices = [db.discovered_device_inventory_item(row) for row in pending_rows]
        if source == "discovered":
            devices = discovered_devices
        else:
            devices.extend(discovered_devices)
        summary["discovered_pending"] = len(discovered_devices)
        summary["total"] = len(devices)
        summary["pending"] = sum(1 for item in devices if item["state"] == "pending")
        summary["unknown"] = sum(1 for item in devices if item["state"] == "unknown")
    if state != "all":
        devices = [item for item in devices if item["state"] == state]
    if source not in {"all", "discovered"}:
        devices = [item for item in devices if item["source"] == source]
    elif source == "all" and auto_import_enabled:
        devices = [item for item in devices if item["source"] != "discovered"]

    device_groups = group_inventory_devices(devices)
    if device_groups:
        summary["total"] = sum(group["count"] for group in device_groups)
    return render_template(
        request,
        "devices/list.html",
        {
            "summary": summary,
            "devices": devices,
            "device_groups": device_groups,
            "selected_state": state,
            "selected_source": source,
            "refresh_seconds": 30,
            "pending_discovery_imports": pending_discovery_imports,
            "auto_import_enabled": auto_import_enabled,
        },
    )


@router.get("/hosts", response_class=HTMLResponse)
async def list_hosts_view(request: Request, user: str = Depends(require_web_permissions("devices.view"))):
    status_list = await db.get_hosts_status()
    for entry in status_list:
        latest = None
        for svc in entry["services"]:
            check = await db.get_latest_check(svc["id"])
            if check and (latest is None or check["checked_at"] > latest):
                latest = check["checked_at"]
        entry["last_check_at"] = latest
    return render_template(request, "hosts/list.html", {"status_list": status_list})


@router.get("/hosts/new", response_class=HTMLResponse)
async def create_host_get(request: Request, user: str = Depends(require_web_permissions("devices.create"))):
    return render_template(request, "hosts/form.html", {"host": None, "error": None})


@router.post("/hosts/new")
async def create_host_post(
    request: Request,
    name: str = Form(...),
    address: str = Form(...),
    host_type: str = Form("server"),
    location: str = Form(""),
    tags: str = Form(""),
    enabled: Optional[str] = Form(None),
    user: str = Depends(require_web_permissions("devices.create")),
):
    form, error = validate_host_form(
        name=name,
        address=address,
        host_type=host_type,
        location=location,
        tags=tags,
        enabled=parse_checkbox(enabled),
    )
    if error:
        return render_template(
            request,
            "hosts/form.html",
            {"host": host_form_as_dict(form) if form else {"name": name, "address": address, "host_type": host_type, "location": location, "tags": tags, "enabled": parse_checkbox(enabled)}, "error": error},
            status_code=400,
        )
    from ditaknet.core.licensing import LicenseLimitError, license_service

    try:
        await license_service.enforce_host_create(address=form.address)
    except LicenseLimitError as exc:
        return render_template(
            request,
            "hosts/form.html",
            {"host": host_form_as_dict(form), "error": str(exc)},
            status_code=403,
        )
    await db.create_host(
        name=form.name,
        address=form.address,
        host_type=form.host_type,
        location=form.location,
        tags=form.tags,
        enabled=form.enabled,
    )
    return RedirectResponse(url="/hosts", status_code=303)


@router.get("/hosts/{host_id}", response_class=HTMLResponse)
async def host_detail(request: Request, host_id: int, user: str = Depends(require_web_permissions("devices.view"))):
    return RedirectResponse(url=f"/devices/host-{host_id}", status_code=301)


@router.get("/hosts/{host_id}/edit", response_class=HTMLResponse)
async def edit_host_get(request: Request, host_id: int, user: str = Depends(require_web_permissions("devices.edit"))):
    host = await db.get_host(host_id)
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    return render_template(request, "hosts/form.html", {"host": host, "error": None})


@router.post("/hosts/{host_id}/edit")
async def edit_host_post(
    request: Request,
    host_id: int,
    name: str = Form(...),
    address: str = Form(...),
    host_type: str = Form("server"),
    location: str = Form(""),
    tags: str = Form(""),
    enabled: Optional[str] = Form(None),
    user: str = Depends(require_web_permissions("devices.edit")),
):
    existing = await db.get_host(host_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Host not found")
    form, error = validate_host_form(
        name=name,
        address=address,
        host_type=host_type,
        location=location,
        tags=tags,
        enabled=parse_checkbox(enabled),
    )
    if error:
        host_view = host_form_as_dict(form) if form else existing
        host_view["id"] = host_id
        return render_template(
            request,
            "hosts/form.html",
            {"host": host_view, "error": error},
            status_code=400,
        )
    from ditaknet.core.licensing import LicenseLimitError, license_service

    try:
        await license_service.enforce_host_network_scope(form.address)
    except LicenseLimitError as exc:
        host_view = host_form_as_dict(form)
        host_view["id"] = host_id
        return render_template(
            request,
            "hosts/form.html",
            {"host": host_view, "error": str(exc)},
            status_code=403,
        )
    await db.update_host(
        host_id,
        name=form.name,
        address=form.address,
        host_type=form.host_type,
        location=form.location,
        tags=form.tags,
        enabled=form.enabled,
    )
    scheduler = get_scheduler()
    for svc in await db.list_services(host_id):
        scheduler.reschedule_service(svc)
    return RedirectResponse(url=f"/devices/host-{host_id}", status_code=303)


@router.post("/hosts/{host_id}/delete")
async def delete_host_action(request: Request, host_id: int, user: str = Depends(require_web_permissions("devices.delete"))):
    scheduler = get_scheduler()
    for svc in await db.list_services(host_id):
        scheduler.remove_service(svc["id"])
    await db.delete_host(host_id)
    return RedirectResponse(url="/hosts", status_code=303)


@router.post("/hosts/{host_id}/disable")
async def disable_host_action(request: Request, host_id: int, user: str = Depends(require_web_permissions("devices.edit"))):
    host = await db.update_host(host_id, enabled=False)
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    scheduler = get_scheduler()
    for svc in await db.list_services(host_id):
        updated = await db.update_service(svc["id"], enabled=False)
        if updated:
            scheduler.reschedule_service(updated)
    return RedirectResponse(url=f"/devices/host-{host_id}", status_code=303)


@router.post("/hosts/{host_id}/run-all")
async def run_all_host_checks(request: Request, host_id: int, user: str = Depends(require_web_permissions("devices.run_check"))):
    host = await db.get_host(host_id)
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    scheduler = get_scheduler()
    for svc in await db.list_services(host_id):
        if svc.get("enabled"):
            await scheduler.trigger_check(svc["id"])
    return RedirectResponse(url=f"/devices/host-{host_id}", status_code=303)


@router.get("/services", response_class=HTMLResponse)
async def list_services_view(request: Request, user: str = Depends(require_web_permissions("services.view"))):
    services = await db.list_services()
    hosts = {h["id"]: h for h in await db.list_hosts()}
    for svc in services:
        svc["host_name"] = hosts.get(svc["host_id"], {}).get("name", "Unknown")
        svc["latest_check"] = await db.get_latest_check(svc["id"])
    return render_template(request, "services/list.html", {"services": services})


@router.get("/services/new", response_class=HTMLResponse)
async def create_service_get(
    request: Request,
    host_id: Optional[int] = Query(None),
    user: str = Depends(require_web_permissions("services.create")),
):
    hosts = await db.list_hosts()
    preset = {"host_id": host_id} if host_id else None
    return render_template(
        request,
        "services/form.html",
        {"service": preset, "hosts": hosts, "error": None},
    )


@router.post("/services/new")
async def create_service_post(
    request: Request,
    host_id: int = Form(...),
    name: str = Form(...),
    check_type: str = Form(...),
    target: str = Form(""),
    url: str = Form(""),
    port: Optional[str] = Form(None),
    interval_seconds: int = Form(60),
    timeout_seconds: int = Form(10),
    retry_count: int = Form(0),
    max_attempts: int = Form(3),
    enabled: Optional[str] = Form(None),
    user: str = Depends(require_web_permissions("services.create")),
):
    final_target = url if check_type == "http" and url else target
    hosts = await db.list_hosts()
    form, error = validate_service_form(
        host_id=host_id,
        name=name,
        check_type=check_type,
        target=final_target,
        port=parse_optional_int(port),
        url=url,
        interval_seconds=interval_seconds,
        timeout_seconds=timeout_seconds,
        retry_count=retry_count,
        max_attempts=max_attempts,
        enabled=parse_checkbox(enabled),
    )
    if error:
        submitted = {
            "host_id": host_id,
            "name": name,
            "check_type": check_type,
            "target": target,
            "url": url,
            "port": parse_optional_int(port),
            "interval_seconds": interval_seconds,
            "timeout_seconds": timeout_seconds,
            "retry_count": retry_count,
            "max_attempts": max_attempts,
            "enabled": parse_checkbox(enabled),
        }
        return render_template(
            request,
            "services/form.html",
            {"service": submitted, "hosts": hosts, "error": error},
            status_code=400,
        )
    from ditaknet.core.licensing import LicenseLimitError, license_service

    try:
        await license_service.enforce_service_create()
    except LicenseLimitError as exc:
        submitted = {
            "host_id": host_id,
            "name": name,
            "check_type": check_type,
            "target": target,
            "url": url,
            "port": parse_optional_int(port),
            "interval_seconds": interval_seconds,
            "timeout_seconds": timeout_seconds,
            "retry_count": retry_count,
            "max_attempts": max_attempts,
            "enabled": parse_checkbox(enabled),
        }
        return render_template(
            request,
            "services/form.html",
            {"service": submitted, "hosts": hosts, "error": str(exc)},
            status_code=403,
        )
    svc = await db.create_service(
        host_id=form.host_id,
        name=form.name,
        check_type=form.check_type,
        target=form.target,
        port=form.port,
        interval_seconds=form.interval_seconds,
        timeout_seconds=form.timeout_seconds,
        retry_count=form.retry_count,
        max_attempts=form.max_attempts,
        enabled=form.enabled,
    )
    if form.enabled:
        get_scheduler().add_service(svc)
    return RedirectResponse(url="/services", status_code=303)


@router.get("/services/{service_id}", response_class=HTMLResponse)
async def service_detail(request: Request, service_id: int, user: str = Depends(require_web_permissions("services.view"))):
    svc = await db.get_service(service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    host = await db.get_host(svc["host_id"])
    results = await db.list_check_results(service_id=service_id, limit=50)
    alerts = await db.list_alerts(service_id=service_id, limit=20)
    _enrich_alerts(alerts, {service_id: svc}, {svc["host_id"]: host or {}})
    latest = results[0] if results else None
    return render_template(
        request,
        "services/detail.html",
        {
            "service": svc,
            "host": host,
            "results": results,
            "alerts": alerts,
            "latest": latest,
        },
    )


@router.get("/services/{service_id}/edit", response_class=HTMLResponse)
async def edit_service_get(request: Request, service_id: int, user: str = Depends(require_web_permissions("services.edit"))):
    svc = await db.get_service(service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    hosts = await db.list_hosts()
    return render_template(
        request,
        "services/form.html",
        {"service": svc, "hosts": hosts, "error": None},
    )


@router.post("/services/{service_id}/edit")
async def edit_service_post(
    request: Request,
    service_id: int,
    host_id: int = Form(...),
    name: str = Form(...),
    check_type: str = Form(...),
    target: str = Form(""),
    port: Optional[str] = Form(None),
    url: str = Form(""),
    interval_seconds: int = Form(60),
    timeout_seconds: int = Form(10),
    retry_count: int = Form(0),
    max_attempts: int = Form(3),
    enabled: Optional[str] = Form(None),
    user: str = Depends(require_web_permissions("services.edit")),
):
    existing = await db.get_service(service_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Service not found")
    hosts = await db.list_hosts()
    form, error = validate_service_form(
        host_id=host_id,
        name=name,
        check_type=check_type,
        target=target,
        port=parse_optional_int(port),
        url=url or target,
        interval_seconds=interval_seconds,
        timeout_seconds=timeout_seconds,
        retry_count=retry_count,
        max_attempts=max_attempts,
        enabled=parse_checkbox(enabled),
    )
    if error:
        submitted = service_form_as_dict(form, service_id=service_id) if form else {
            **existing,
            "host_id": host_id,
            "name": name,
            "check_type": check_type,
            "target": target,
            "port": parse_optional_int(port),
            "interval_seconds": interval_seconds,
            "timeout_seconds": timeout_seconds,
            "retry_count": retry_count,
            "max_attempts": max_attempts,
            "enabled": parse_checkbox(enabled),
        }
        return render_template(
            request,
            "services/form.html",
            {"service": submitted, "hosts": hosts, "error": error},
            status_code=400,
        )
    svc = await db.update_service(
        service_id,
        host_id=form.host_id,
        name=form.name,
        check_type=form.check_type,
        target=form.target,
        port=form.port,
        interval_seconds=form.interval_seconds,
        timeout_seconds=form.timeout_seconds,
        retry_count=form.retry_count,
        max_attempts=form.max_attempts,
        enabled=form.enabled,
    )
    get_scheduler().reschedule_service(svc)
    return RedirectResponse(url=f"/services/{service_id}", status_code=303)


@router.post("/services/{service_id}/delete")
async def delete_service_action(request: Request, service_id: int, user: str = Depends(require_web_permissions("services.delete"))):
    if not await db.get_service(service_id):
        raise HTTPException(status_code=404, detail="Service not found")
    get_scheduler().remove_service(service_id)
    await db.delete_service(service_id)
    return RedirectResponse(url="/services", status_code=303)


@router.post("/services/{service_id}/run")
@router.post("/services/{service_id}/run-now")
async def run_service_action(request: Request, service_id: int, user: str = Depends(require_web_permissions("devices.run_check"))):
    if not await db.get_service(service_id):
        raise HTTPException(status_code=404, detail="Service not found")
    await get_scheduler().trigger_check(service_id)
    referer = request.headers.get("referer", f"/services/{service_id}")
    return RedirectResponse(url=referer, status_code=303)


@router.post("/services/{service_id}/disable")
async def disable_service_action(request: Request, service_id: int, user: str = Depends(require_web_permissions("services.edit"))):
    svc = await db.update_service(service_id, enabled=False)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    get_scheduler().reschedule_service(svc)
    referer = request.headers.get("referer", f"/services/{service_id}")
    return RedirectResponse(url=referer, status_code=303)


@router.get("/alerts", response_class=HTMLResponse)
async def list_alerts_view(
    request: Request,
    filter: str = Query("all"),
    user: str = Depends(require_web_permissions("alerts.view")),
):
    alerts = await db.list_alerts(limit=200)
    services = {s["id"]: s for s in await db.list_services()}
    hosts = {h["id"]: h for h in await db.list_hosts()}
    _enrich_alerts(alerts, services, hosts)
    if filter == "active":
        alerts = [a for a in alerts if a["is_active"]]
    elif filter == "resolved":
        alerts = [a for a in alerts if not a["is_active"]]
    active_alerts = [a for a in alerts if a["is_active"]]
    resolved_alerts = [a for a in alerts if not a["is_active"]]
    return render_template(
        request,
        "alerts/list.html",
        {
            "alerts": alerts,
            "active_alerts": active_alerts,
            "resolved_alerts": resolved_alerts,
            "filter": filter,
        },
    )


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert_action(request: Request, alert_id: int, user: str = Depends(require_web_permissions("alerts.acknowledge"))):
    if not await db.get_alert(alert_id):
        raise HTTPException(status_code=404, detail="Alert not found")
    await db.acknowledge_alert(alert_id)
    return RedirectResponse(url="/alerts", status_code=303)


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert_action(request: Request, alert_id: int, user: str = Depends(require_web_permissions("alerts.acknowledge"))):
    if not await db.get_alert(alert_id):
        raise HTTPException(status_code=404, detail="Alert not found")
    await db.resolve_alert(alert_id)
    return RedirectResponse(url="/alerts", status_code=303)


@router.get("/results", response_class=HTMLResponse)
async def list_results_view(
    request: Request,
    host_id: Optional[int] = Query(None),
    service_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    user: str = Depends(require_web_permissions("results.view")),
):
    results = await db.list_check_results(
        service_id=service_id,
        host_id=host_id,
        status=status,
        limit=100,
    )
    services = {s["id"]: s for s in await db.list_services()}
    hosts = {h["id"]: h for h in await db.list_hosts()}
    _enrich_checks(results, services, hosts)
    return render_template(
        request,
        "results/list.html",
        {
            "results": results,
            "hosts": await db.list_hosts(),
            "services": await db.list_services(),
            "selected_host_id": host_id,
            "selected_service_id": service_id,
            "selected_status": status,
        },
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_view(request: Request, user: str = Depends(require_web_permissions("settings.view"))):
    try:
        scheduler = get_scheduler()
        sched_obj = getattr(scheduler, "_scheduler", None)
        scheduler_running = bool(getattr(sched_obj, "running", False))
        scheduler_jobs = len(sched_obj.get_jobs()) if sched_obj and hasattr(sched_obj, "get_jobs") else 0
    except RuntimeError:
        scheduler_running = False
        scheduler_jobs = 0
    update_status = {}
    try:
        from ditaknet.core.updates import get_update_status

        update_status = await get_update_status()
    except Exception as exc:
        logger.warning("Settings update status failed: {}", exc)
    return render_template(
        request,
        "settings/index.html",
        {
            "settings": settings,
            "scheduler_running": scheduler_running,
            "scheduler_jobs": scheduler_jobs,
            "host_count": len(await db.list_hosts()),
            "service_count": len(await db.list_services()),
            "active_alerts": await db.count_active_alerts(),
            "failed_checks_24h": await db.count_failed_checks_since(hours=24),
            "update_status": update_status,
            "active": "general",
        },
    )


@router.get("/settings/system", response_class=HTMLResponse)
async def settings_system_view(request: Request, user: str = Depends(require_web_permissions("settings.view"))):
    from ditaknet.api.v1.system import _scheduler_payload
    from ditaknet.core.system_metrics import collect_system_metrics
    from ditaknet.core.updates import get_update_status
    from ditaknet.health import deep_health

    return render_template(
        request,
        "settings/system.html",
        {
            "settings": settings,
            "active": "system",
            "health": await deep_health(),
            "metrics": collect_system_metrics(),
            "scheduler": await _scheduler_payload(),
            "maintenance_mode": await db.get_maintenance_mode(settings.maintenance_mode),
            "update_status": await get_update_status(),
        },
    )


@router.post("/settings/system/maintenance")
async def settings_system_maintenance_post(
    request: Request,
    enabled: Optional[str] = Form(None),
    user: str = Depends(require_web_permissions("settings.edit")),
):
    value = parse_checkbox(enabled)
    await db.set_maintenance_mode(value)
    try:
        await db.create_audit_log(
            "maintenance.update",
            actor=user,
            resource="maintenance",
            detail=f"enabled={value}",
            ip_address=request.client.host if request.client else "",
        )
    except Exception as exc:
        logger.warning("Failed to write maintenance audit log: {}", exc)
    return RedirectResponse(url="/settings/system", status_code=303)


def _linux_capability_enabled(bit: int) -> bool:
    try:
        with open("/proc/self/status", encoding="utf-8") as status_file:
            for line in status_file:
                if line.startswith("CapEff:"):
                    value = int(line.split(":", 1)[1].strip(), 16)
                    return bool(value & (1 << bit))
    except Exception:
        return False
    return False


def _runtime_network_summary() -> dict:
    import shutil
    from pathlib import Path

    in_container = Path("/.dockerenv").exists()
    if not in_container:
        try:
            cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="ignore").lower()
            in_container = any(token in cgroup for token in ("docker", "containerd", "kubepods"))
        except Exception:
            in_container = False
    return {
        "in_container": in_container,
        "deployment_mode": settings.app_deployment_mode.strip() or ("container" if in_container else "host"),
        "has_ping": bool(shutil.which("ping")),
        "has_ip_tool": bool(shutil.which("ip")),
        "cap_net_raw": _linux_capability_enabled(13),
        "cap_net_admin": _linux_capability_enabled(12),
    }


@router.get("/settings/discovery", response_class=HTMLResponse)
async def settings_discovery_view(request: Request, user: str = Depends(require_web_permissions("settings.view"))):
    from ditaknet.discovery import store as discovery_store

    return render_template(
        request,
        "settings/discovery.html",
        {
            "settings": settings,
            "active": "discovery",
            "discovery_settings": await discovery_store.get_discovery_settings(),
            "networks": await discovery_store.list_monitored_networks(enabled_only=False),
            "scans": await db.list_discovery_scans(limit=8),
            "runtime_network": _runtime_network_summary(),
        },
    )


@router.post("/settings/discovery")
async def settings_discovery_post(
    request: Request,
    auto_refresh_enabled: Optional[str] = Form(None),
    refresh_interval_minutes: int = Form(10),
    stale_after_minutes: int = Form(30),
    offline_after_minutes: int = Form(60),
    scan_mode: str = Form("normal"),
    user: str = Depends(require_web_permissions("settings.edit")),
):
    from ditaknet.discovery import store as discovery_store

    await discovery_store.update_discovery_settings(
        auto_refresh_enabled=parse_checkbox(auto_refresh_enabled),
        refresh_interval_minutes=max(1, refresh_interval_minutes),
        stale_after_minutes=max(1, stale_after_minutes),
        offline_after_minutes=max(1, offline_after_minutes),
        scan_mode=scan_mode if scan_mode in {"quick", "normal", "deep"} else "normal",
    )
    try:
        await db.create_audit_log(
            "discovery.settings.update",
            actor=user,
            resource="discovery_settings",
            ip_address=request.client.host if request.client else "",
        )
    except Exception as exc:
        logger.warning("Failed to write discovery settings audit log: {}", exc)
    return RedirectResponse(url="/settings/discovery", status_code=303)


async def _safe_table_count(table: str) -> int:
    allowed = {
        "hosts",
        "services",
        "check_results",
        "alerts",
        "discovery_scans",
        "discovered_devices",
        "discovery_inventory",
        "system_logs",
        "audit_logs",
    }
    if table not in allowed:
        return 0
    try:
        conn = await db.get_db()
        rows = await conn.execute_fetchall(f"SELECT COUNT(*) AS cnt FROM {table}")
        return int(rows[0]["cnt"] if rows else 0)
    except Exception:
        return 0


@router.get("/settings/security", response_class=HTMLResponse)
async def settings_security_view(request: Request, user: str = Depends(require_web_permissions("settings.security"))):
    stored_admin = await db.get_app_setting("admin_username")
    stored_hash = await db.get_app_setting("admin_password_hash")
    return render_template(
        request,
        "settings/security.html",
        {
            "settings": settings,
            "active": "security",
            "stored_admin": stored_admin or "",
            "stored_admin_password_configured": bool(stored_hash),
            "effective_secret_configured": bool(settings.effective_secret_key and settings.effective_secret_key != "change-me"),
            "cors_origins": settings.cors_origins,
            "audit_logs": await db.list_audit_logs(limit=10, offset=0),
            "errors_last_24h": await db.count_system_logs_since(hours=24, levels=["error", "critical"]),
            "warnings_last_24h": await db.count_system_logs_since(hours=24, level="warning"),
        },
    )


@router.get("/settings/data", response_class=HTMLResponse)
async def settings_data_view(
    request: Request,
    msg: str = Query(""),
    error: str = Query(""),
    user: str = Depends(require_web_permissions("settings.view")),
):
    from ditaknet.core.backup import list_backups
    from ditaknet.health import deep_health

    counts = {
        "hosts": await _safe_table_count("hosts"),
        "services": await _safe_table_count("services"),
        "check_results": await _safe_table_count("check_results"),
        "alerts": await _safe_table_count("alerts"),
        "discovery_scans": await _safe_table_count("discovery_scans"),
        "discovered_devices": await _safe_table_count("discovered_devices"),
        "inventory": await _safe_table_count("discovery_inventory"),
        "system_logs": await _safe_table_count("system_logs"),
        "audit_logs": await _safe_table_count("audit_logs"),
    }
    return render_template(
        request,
        "settings/data.html",
        {
            "settings": settings,
            "active": "data",
            "health": await deep_health(),
            "counts": counts,
            "backups": list_backups(),
            "message": msg,
            "error": error,
        },
    )


@router.post("/settings/data/backup")
async def settings_data_backup_post(request: Request, user: str = Depends(require_web_permissions("backups.create"))):
    from ditaknet.core.backup import create_backup

    try:
        backup = create_backup()
        await db.create_audit_log(
            "backup.create",
            actor=user,
            resource="backup",
            resource_id=backup["filename"],
            ip_address=request.client.host if request.client else "",
        )
        return RedirectResponse(url=f"/settings/data?msg={backup['filename']}", status_code=303)
    except Exception as exc:
        logger.error("Manual backup failed: {}", exc)
        return RedirectResponse(url=f"/settings/data?error={type(exc).__name__}", status_code=303)


@router.get("/settings/telegram", response_class=HTMLResponse)
async def settings_telegram_view(request: Request, user: str = Depends(require_web_permissions("settings.view"))):
    return render_template(
        request,
        "settings/telegram.html",
        {
            "settings": settings,
            "active": "telegram",
            "sent": request.query_params.get("sent") == "1",
        },
    )


@router.get("/settings/reset", response_class=HTMLResponse)
async def settings_reset_get(request: Request, user: str = Depends(require_web_permissions("settings.security"))):
    return render_template(
        request,
        "settings/reset.html",
        {
            "active": "reset",
        },
    )


@router.post("/settings/reset")
async def settings_reset_post(
    request: Request,
    confirmation: str = Form(""),
    user: str = Depends(require_web_permissions("settings.security")),
):
    from ditaknet.i18n import translate

    lang = request.session.get("lang", "en")
    def t(k: str, **kw):
        return translate(k, lang, **kw)
    try:
        from ditaknet.core.system_reset import factory_reset_to_setup

        await factory_reset_to_setup(
            actor=user,
            ip_address=request.client.host if request.client else "",
            confirmation=confirmation,
        )
    except ValueError:
        return render_template(
            request,
            "settings/reset.html",
            {
                "active": "reset",
                "lang": lang,
                "t": t,
                "error": t("settings.reset.error.confirm"),
            },
            status_code=400,
        )
    except RuntimeError as exc:
        return render_template(
            request,
            "settings/reset.html",
            {
                "active": "reset",
                "lang": lang,
                "t": t,
                "error": str(exc),
            },
            status_code=500,
        )
    request.session.clear()
    return RedirectResponse(url="/setup", status_code=303)


@router.get("/settings/backups", response_class=HTMLResponse)
async def settings_backups_view(request: Request, user: str = Depends(require_web_permissions("backups.view"))):
    return render_template(
        request,
        "settings/backups.html",
        {
            "settings": settings,
            "active": "backups",
        },
    )


@router.get("/notifications", response_class=HTMLResponse)
async def notifications_page(request: Request, user: str = Depends(require_web_permissions("alerts.view"))):
    return render_template(
        request,
        "notifications.html",
        {},
    )


@router.post("/settings/telegram/test")
async def settings_telegram_test(request: Request, user: str = Depends(require_web_permissions("settings.edit"))):
    alert_engine = get_alert_engine()
    sent = False
    for notifier in alert_engine._notifiers:
        try:
            if hasattr(notifier, "send"):
                sent = await notifier.send(
                    subject="DitakNet Test",
                    message="DitakNet test notification",
                    severity="warning",
                ) or sent
        except Exception as exc:
            logger.error("Test notification failed: {}", exc)
    if not sent:
        logger.warning("DitakNet test notification (console fallback — no notifiers configured)")
    return RedirectResponse(url="/settings/telegram?sent=1", status_code=303)
