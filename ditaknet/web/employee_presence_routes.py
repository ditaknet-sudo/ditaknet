"""Employee Presence Monitoring web pages."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ditaknet.core import employee_presence as presence
from ditaknet.core.licensing import LicenseLimitError, license_service
from ditaknet.i18n import translate
from ditaknet.web.routes import get_current_user, render_template

router = APIRouter(include_in_schema=False)


def _ctx(request: Request, **extra):
    lang = request.session.get("lang", "en")
    base = {"lang": lang, "t": lambda k, **kw: translate(k, lang, **kw)}
    base.update(extra)
    return base


async def _licensed() -> bool:
    return bool((await license_service.status()).get("employee_presence_enabled"))


async def _locked_context(request: Request) -> dict:
    return _ctx(request, license=await license_service.status())


@router.get("/employee-presence", response_class=HTMLResponse)
async def employee_presence_dashboard(request: Request, user: str = Depends(get_current_user)):
    if not await _licensed():
        return render_template(
            request,
            "employee_presence/locked.html",
            await _locked_context(request),
            status_code=403,
        )
    summary = await presence.summarize_presence()
    rows = await presence.list_employee_presence()
    return render_template(
        request,
        "employee_presence/dashboard.html",
        _ctx(request, summary=summary, rows=rows),
    )


@router.post("/employee-presence/refresh")
async def employee_presence_refresh(request: Request, user: str = Depends(get_current_user)):
    try:
        await presence.refresh_presence()
    except LicenseLimitError:
        return RedirectResponse(url="/employee-presence", status_code=303)
    return RedirectResponse(url="/employee-presence", status_code=303)


@router.get("/employees", response_class=HTMLResponse)
async def employees_list(
    request: Request,
    search: str = Query(""),
    department: str = Query(""),
    status: str = Query(""),
    user: str = Depends(get_current_user),
):
    if not await _licensed():
        return render_template(
            request,
            "employee_presence/locked.html",
            await _locked_context(request),
            status_code=403,
        )
    employees = await presence.list_employees(search=search, department=department, status=status)
    presence_rows = {row["employee_id"]: row for row in await presence.list_employee_presence()}
    return render_template(
        request,
        "employee_presence/employees.html",
        _ctx(
            request,
            employees=employees,
            presence_rows=presence_rows,
            search=search,
            department=department,
            selected_status=status,
            summary=await presence.summarize_presence(),
        ),
    )


@router.get("/employees/create", response_class=HTMLResponse)
async def employee_create_get(request: Request, user: str = Depends(get_current_user)):
    if not await _licensed():
        return render_template(
            request,
            "employee_presence/locked.html",
            await _locked_context(request),
            status_code=403,
        )
    return render_template(
        request, "employee_presence/employee_form.html", _ctx(request, employee=None, error="")
    )


@router.post("/employees/create")
async def employee_create_post(
    request: Request,
    full_name: str = Form(...),
    department: str = Form(""),
    position: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    employee_code: str = Form(""),
    privacy_notice_accepted: str = Form(""),
    user: str = Depends(get_current_user),
):
    try:
        employee = await presence.create_employee(
            full_name=full_name,
            department=department,
            position=position,
            email=email,
            phone=phone,
            employee_code=employee_code,
            privacy_notice_accepted=bool(privacy_notice_accepted),
            actor=user,
        )
    except (LicenseLimitError, ValueError) as exc:
        return render_template(
            request,
            "employee_presence/employee_form.html",
            _ctx(request, employee=None, error=str(exc)),
            status_code=400,
        )
    return RedirectResponse(url=f"/employees/{employee['id']}", status_code=303)


@router.get("/employees/{employee_id}", response_class=HTMLResponse)
async def employee_detail(
    employee_id: int, request: Request, user: str = Depends(get_current_user)
):
    if not await _licensed():
        return render_template(
            request,
            "employee_presence/locked.html",
            await _locked_context(request),
            status_code=403,
        )
    employee = await presence.get_employee(employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    return render_template(
        request,
        "employee_presence/employee_detail.html",
        _ctx(
            request,
            employee=employee,
            devices=await presence.list_employee_devices(employee_id),
            presence=await presence.get_employee_presence(employee_id),
            events=await presence.list_presence_events(employee_id, limit=50),
        ),
    )


@router.post("/employees/{employee_id}/manual-status")
async def employee_manual_status(
    employee_id: int,
    request: Request,
    status: str = Form(...),
    notes: str = Form(""),
    user: str = Depends(get_current_user),
):
    try:
        await presence.manual_status_update(
            employee_id=employee_id,
            status=status,
            connection_type="manual",
            confidence="medium",
            notes=notes,
            actor=user,
        )
    except (LicenseLimitError, ValueError):
        pass
    return RedirectResponse(url=f"/employees/{employee_id}", status_code=303)


@router.get("/employees/{employee_id}/devices", response_class=HTMLResponse)
async def employee_devices_get(
    employee_id: int, request: Request, user: str = Depends(get_current_user)
):
    if not await _licensed():
        return render_template(
            request,
            "employee_presence/locked.html",
            await _locked_context(request),
            status_code=403,
        )
    employee = await presence.get_employee(employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    return render_template(
        request,
        "employee_presence/device_form.html",
        _ctx(request, employee=employee, error=""),
    )


@router.post("/employees/{employee_id}/devices")
async def employee_devices_post(
    employee_id: int,
    request: Request,
    device_name: str = Form(...),
    device_type: str = Form("laptop"),
    mac_address: str = Form(""),
    hostname: str = Form(""),
    static_ip: str = Form(""),
    agent_id: str = Form(""),
    is_primary: str = Form(""),
    user: str = Depends(get_current_user),
):
    try:
        await presence.create_employee_device(
            employee_id=employee_id,
            device_name=device_name,
            device_type=device_type,
            mac_address=mac_address,
            hostname=hostname,
            static_ip=static_ip,
            last_ip=static_ip,
            agent_id=agent_id,
            is_primary=bool(is_primary),
            is_approved=True,
            actor=user,
        )
    except (LicenseLimitError, ValueError) as exc:
        employee = await presence.get_employee(employee_id)
        return render_template(
            request,
            "employee_presence/device_form.html",
            _ctx(request, employee=employee, error=str(exc)),
            status_code=400,
        )
    return RedirectResponse(url=f"/employees/{employee_id}", status_code=303)


@router.get("/settings/employee-presence", response_class=HTMLResponse)
async def employee_presence_settings_get(request: Request, user: str = Depends(get_current_user)):
    if not await _licensed():
        return render_template(
            request,
            "employee_presence/locked.html",
            await _locked_context(request),
            status_code=403,
        )
    return render_template(
        request,
        "employee_presence/settings.html",
        _ctx(request, settings=await presence.get_presence_settings(), error="", saved=False),
    )


@router.post("/settings/employee-presence")
async def employee_presence_settings_post(
    request: Request,
    configured_enabled: str = Form(""),
    presence_online_grace_minutes: int = Form(5),
    presence_away_after_minutes: int = Form(15),
    presence_offline_after_minutes: int = Form(60),
    privacy_notice_text: str = Form(presence.DEFAULT_PRIVACY_NOTICE),
    user: str = Depends(get_current_user),
):
    try:
        settings = await presence.update_presence_settings(
            configured_enabled=bool(configured_enabled),
            presence_online_grace_minutes=presence_online_grace_minutes,
            presence_away_after_minutes=presence_away_after_minutes,
            presence_offline_after_minutes=presence_offline_after_minutes,
            privacy_notice_text=privacy_notice_text,
            actor=user,
        )
    except (LicenseLimitError, ValueError) as exc:
        return render_template(
            request,
            "employee_presence/settings.html",
            _ctx(
                request,
                settings=await presence.get_presence_settings(),
                error=str(exc),
                saved=False,
            ),
            status_code=400,
        )
    return render_template(
        request,
        "employee_presence/settings.html",
        _ctx(request, settings=settings, error="", saved=True),
    )
