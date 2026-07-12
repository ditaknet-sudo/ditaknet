"""Employee Attendance & Presence web UI (separate from device monitoring)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from ditaknet.core import hr
from ditaknet.core.hr.access import get_user_department_scope
from ditaknet.core.licensing import LicenseLimitError, license_service
from ditaknet.i18n import translate
from ditaknet.security import AuthenticatedUser, has_hr_permission, has_office_permission, user_from_session
from ditaknet.web.routes import get_current_user, render_template

router = APIRouter(include_in_schema=False)


def _ctx(request: Request, **extra):
    lang = request.session.get("lang", "en")
    user = user_from_session(request)
    role = user.role if user else "viewer"
    base = {
        "lang": lang,
        "t": lambda k, **kw: translate(k, lang, **kw),
        "hr_role": role,
        "can_manage_employees": has_hr_permission(role, "hr.manage_employees"),
        "can_edit_attendance": has_hr_permission(role, "hr.edit_attendance"),
        "can_view_attendance": has_hr_permission(role, "hr.view_attendance"),
        "can_export": has_hr_permission(role, "hr.export_attendance_reports"),
        "can_manage_settings": has_hr_permission(role, "hr.manage_attendance_settings"),
    }
    base.update(extra)
    return base


async def _licensed() -> bool:
    return bool((await license_service.status()).get("employee_presence_enabled"))


async def _multi_office_licensed() -> bool:
    return bool((await license_service.status()).get("multi_office_enabled"))


async def _locked(request: Request) -> HTMLResponse:
    return render_template(
        request,
        "hr/locked.html",
        _ctx(request, license=await license_service.status()),
        status_code=403,
    )


def _no_permission(request: Request) -> HTMLResponse:
    return render_template(request, "hr/no_permission.html", _ctx(request), status_code=403)


async def _session_user(request: Request) -> AuthenticatedUser:
    user = user_from_session(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


async def _multi_office_licensed() -> bool:
    status = await license_service.status()
    return bool(status.get("multi_office_enabled"))


@router.get("/attendance", response_class=HTMLResponse)
async def attendance_daily(
    request: Request,
    day: str = Query(""),
    department_id: str = Query(""),
    group_id: str = Query(""),
    shift_id: str = Query(""),
    office_id: str = Query(""),
    search: str = Query(""),
    status: str = Query(""),
    confidence: str = Query(""),
    user: str = Depends(get_current_user),
):
    if not await _licensed():
        return await _locked(request)
    auth = await _session_user(request)
    if not has_hr_permission(auth.role, "hr.view_attendance"):
        return _no_permission(request)
    target = date.fromisoformat(day) if day else date.today()
    scope = await get_user_department_scope(auth)
    rows = await hr.list_attendance_days(
        day=target,
        department_id=int(department_id) if department_id else None,
        group_id=int(group_id) if group_id else None,
        shift_id=int(shift_id) if shift_id else None,
        office_id=int(office_id) if office_id else None,
        search=search,
        status=status,
        confidence=confidence,
        department_ids=scope,
    )
    return render_template(
        request,
        "hr/attendance_daily.html",
        _ctx(
            request,
            rows=rows,
            selected_day=target.isoformat(),
            departments=await hr.list_departments(active_only=True),
            groups=await hr.list_employee_groups(active_only=True),
            shifts=await hr.list_shifts(active_only=True),
            offices=await _list_offices_safe(),
            filters={
                "department_id": department_id,
                "group_id": group_id,
                "shift_id": shift_id,
                "office_id": office_id,
                "search": search,
                "status": status,
                "confidence": confidence,
            },
            settings=await hr.get_attendance_settings(),
        ),
    )


@router.post("/attendance/refresh")
async def attendance_refresh(request: Request, day: str = Form(""), user: str = Depends(get_current_user)):
    if not await _licensed():
        return await _locked(request)
    auth = await _session_user(request)
    if not has_hr_permission(auth.role, "hr.edit_attendance"):
        return _no_permission(request)
    try:
        target = date.fromisoformat(day) if day else None
        await hr.refresh_attendance_for_date(target)
    except LicenseLimitError:
        pass
    redirect_day = day or date.today().isoformat()
    return RedirectResponse(url=f"/attendance?day={redirect_day}", status_code=303)


@router.get("/attendance/reports/monthly", response_class=HTMLResponse)
async def attendance_monthly_report(
    request: Request,
    month: str = Query(""),
    department_id: str = Query(""),
    group_id: str = Query(""),
    employee_id: str = Query(""),
    user: str = Depends(get_current_user),
):
    if not await _licensed():
        return await _locked(request)
    auth = await _session_user(request)
    if not has_hr_permission(auth.role, "hr.view_attendance"):
        return _no_permission(request)
    if not month:
        today = date.today()
        month = f"{today.year:04d}-{today.month:02d}"
    scope = await get_user_department_scope(auth)
    report = await hr.monthly_report(
        month=month,
        department_id=int(department_id) if department_id else None,
        group_id=int(group_id) if group_id else None,
        employee_id=int(employee_id) if employee_id else None,
        department_ids=scope,
        actor=auth.username,
    )
    return render_template(
        request,
        "hr/attendance_monthly.html",
        _ctx(
            request,
            report=report,
            month=month,
            departments=await hr.list_departments(active_only=True),
            groups=await hr.list_employee_groups(active_only=True),
            employees=await hr.list_employees(department_ids=scope),
            filters={"department_id": department_id, "group_id": group_id, "employee_id": employee_id},
        ),
    )


@router.get("/attendance/reports/monthly/export.csv")
async def attendance_monthly_export(
    request: Request,
    month: str = Query(...),
    department_id: str = Query(""),
    group_id: str = Query(""),
    employee_id: str = Query(""),
    user: str = Depends(get_current_user),
):
    if not await _licensed():
        raise HTTPException(status_code=403, detail="Feature not included in current package")
    auth = await _session_user(request)
    if not has_hr_permission(auth.role, "hr.export_attendance_reports"):
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    scope = await get_user_department_scope(auth)
    csv_data = await hr.export_monthly_csv(
        month=month,
        department_id=int(department_id) if department_id else None,
        group_id=int(group_id) if group_id else None,
        employee_id=int(employee_id) if employee_id else None,
        department_ids=scope,
        actor=auth.username,
    )
    return PlainTextResponse(csv_data, media_type="text/csv")


@router.get("/attendance/settings", response_class=HTMLResponse)
async def attendance_settings_get(request: Request, user: str = Depends(get_current_user)):
    if not await _licensed():
        return await _locked(request)
    auth = await _session_user(request)
    if not has_hr_permission(auth.role, "hr.manage_attendance_settings"):
        return _no_permission(request)
    return render_template(
        request,
        "hr/settings.html",
        _ctx(request, settings=await hr.get_attendance_settings(), shifts=await hr.list_shifts(), error="", saved=False),
    )


@router.post("/attendance/settings")
async def attendance_settings_post(
    request: Request,
    enable_employee_attendance: str = Form(""),
    default_shift_id: str = Form(""),
    presence_online_grace_minutes: int = Form(5),
    presence_away_after_minutes: int = Form(15),
    presence_offline_after_minutes: int = Form(60),
    ignore_gap_minutes: int = Form(5),
    count_remote_as_work_time: str = Form(""),
    require_high_confidence_for_auto_attendance: str = Form(""),
    allow_ip_only_attendance: str = Form(""),
    allow_manual_corrections: str = Form("1"),
    export_reports_enabled: str = Form("1"),
    privacy_notice_required: str = Form("1"),
    privacy_notice_text: str = Form(""),
    user: str = Depends(get_current_user),
):
    if not await _licensed():
        return await _locked(request)
    auth = await _session_user(request)
    if not has_hr_permission(auth.role, "hr.manage_attendance_settings"):
        return _no_permission(request)
    try:
        settings = await hr.update_attendance_settings(
            actor=auth.username,
            enable_employee_attendance=bool(enable_employee_attendance),
            default_shift_id=int(default_shift_id) if default_shift_id else None,
            presence_online_grace_minutes=presence_online_grace_minutes,
            presence_away_after_minutes=presence_away_after_minutes,
            presence_offline_after_minutes=presence_offline_after_minutes,
            ignore_gap_minutes=ignore_gap_minutes,
            count_remote_as_work_time=bool(count_remote_as_work_time),
            require_high_confidence_for_auto_attendance=bool(require_high_confidence_for_auto_attendance),
            allow_ip_only_attendance=bool(allow_ip_only_attendance),
            allow_manual_corrections=bool(allow_manual_corrections),
            export_reports_enabled=bool(export_reports_enabled),
            privacy_notice_required=bool(privacy_notice_required),
            privacy_notice_text=privacy_notice_text,
        )
    except (LicenseLimitError, ValueError) as exc:
        return render_template(
            request,
            "hr/settings.html",
            _ctx(
                request,
                settings=await hr.get_attendance_settings(),
                shifts=await hr.list_shifts(),
                error=str(exc),
                saved=False,
            ),
            status_code=400,
        )
    return render_template(
        request,
        "hr/settings.html",
        _ctx(request, settings=settings, shifts=await hr.list_shifts(), error="", saved=True),
    )


@router.get("/departments", response_class=HTMLResponse)
async def departments_list(request: Request, user: str = Depends(get_current_user)):
    if not await _licensed():
        return await _locked(request)
    auth = await _session_user(request)
    if not has_hr_permission(auth.role, "hr.view"):
        return _no_permission(request)
    return render_template(request, "hr/departments.html", _ctx(request, departments=await hr.list_departments()))


@router.get("/departments/create", response_class=HTMLResponse)
async def department_create_get(request: Request, user: str = Depends(get_current_user)):
    if not await _licensed():
        return await _locked(request)
    if not has_hr_permission((await _session_user(request)).role, "hr.manage_departments"):
        return _no_permission(request)
    return render_template(request, "hr/department_form.html", _ctx(request, department=None, error=""))


@router.post("/departments/create")
async def department_create_post(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    manager_user_id: str = Form(""),
    user: str = Depends(get_current_user),
):
    if not await _licensed():
        return await _locked(request)
    auth = await _session_user(request)
    if not has_hr_permission(auth.role, "hr.manage_departments"):
        return _no_permission(request)
    await hr.create_department(name=name, description=description, manager_user_id=manager_user_id, actor=auth.username)
    return RedirectResponse(url="/departments", status_code=303)


@router.get("/employee-groups", response_class=HTMLResponse)
async def groups_list(request: Request, user: str = Depends(get_current_user)):
    if not await _licensed():
        return await _locked(request)
    auth = await _session_user(request)
    if not has_hr_permission(auth.role, "hr.view"):
        return _no_permission(request)
    return render_template(
        request,
        "hr/groups.html",
        _ctx(request, groups=await hr.list_employee_groups(), departments=await hr.list_departments()),
    )


@router.get("/employee-groups/create", response_class=HTMLResponse)
async def group_create_get(request: Request, user: str = Depends(get_current_user)):
    if not await _licensed():
        return await _locked(request)
    auth = await _session_user(request)
    if not has_hr_permission(auth.role, "hr.manage_groups"):
        return _no_permission(request)
    return render_template(
        request,
        "hr/group_form.html",
        _ctx(request, group=None, departments=await hr.list_departments(), shifts=await hr.list_shifts(), error=""),
    )


@router.post("/employee-groups/create")
async def group_create_post(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    department_id: str = Form(""),
    default_shift_id: str = Form(""),
    user: str = Depends(get_current_user),
):
    if not await _licensed():
        return await _locked(request)
    auth = await _session_user(request)
    if not has_hr_permission(auth.role, "hr.manage_groups"):
        return _no_permission(request)
    await hr.create_employee_group(
        name=name,
        description=description,
        department_id=int(department_id) if department_id else None,
        default_shift_id=int(default_shift_id) if default_shift_id else None,
        actor=auth.username,
    )
    return RedirectResponse(url="/employee-groups", status_code=303)


@router.get("/shifts", response_class=HTMLResponse)
async def shifts_list(request: Request, user: str = Depends(get_current_user)):
    if not await _licensed():
        return await _locked(request)
    auth = await _session_user(request)
    if not has_hr_permission(auth.role, "hr.view"):
        return _no_permission(request)
    return render_template(request, "hr/shifts.html", _ctx(request, shifts=await hr.list_shifts()))


@router.get("/shifts/create", response_class=HTMLResponse)
async def shift_create_get(request: Request, user: str = Depends(get_current_user)):
    if not await _licensed():
        return await _locked(request)
    auth = await _session_user(request)
    if not has_hr_permission(auth.role, "hr.manage_shifts"):
        return _no_permission(request)
    return render_template(request, "hr/shift_form.html", _ctx(request, shift=None, error=""))


@router.post("/shifts/create")
async def shift_create_post(
    request: Request,
    name: str = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    timezone: str = Form("UTC"),
    break_minutes: int = Form(0),
    grace_late_minutes: int = Form(10),
    grace_leave_early_minutes: int = Form(10),
    expected_work_minutes: int = Form(480),
    is_overnight: str = Form(""),
    user: str = Depends(get_current_user),
):
    if not await _licensed():
        return await _locked(request)
    auth = await _session_user(request)
    if not has_hr_permission(auth.role, "hr.manage_shifts"):
        return _no_permission(request)
    await hr.create_shift(
        name=name,
        start_time=start_time,
        end_time=end_time,
        timezone=timezone,
        break_minutes=break_minutes,
        grace_late_minutes=grace_late_minutes,
        grace_leave_early_minutes=grace_leave_early_minutes,
        expected_work_minutes=expected_work_minutes,
        is_overnight=bool(is_overnight),
        actor=auth.username,
    )
    return RedirectResponse(url="/shifts", status_code=303)


@router.get("/employees", response_class=HTMLResponse)
async def employees_list_hr(
    request: Request,
    search: str = Query(""),
    department_id: str = Query(""),
    group_id: str = Query(""),
    employment_status: str = Query(""),
    user: str = Depends(get_current_user),
):
    if not await _licensed():
        return await _locked(request)
    auth = await _session_user(request)
    if not has_hr_permission(auth.role, "hr.view"):
        return _no_permission(request)
    scope = await get_user_department_scope(auth)
    employees = await hr.list_employees(
        search=search,
        department_id=int(department_id) if department_id else None,
        group_id=int(group_id) if group_id else None,
        employment_status=employment_status,
        department_ids=scope,
    )
    return render_template(
        request,
        "hr/employees.html",
        _ctx(
            request,
            employees=employees,
            departments=await hr.list_departments(active_only=True),
            groups=await hr.list_employee_groups(active_only=True),
            filters={"search": search, "department_id": department_id, "group_id": group_id, "employment_status": employment_status},
        ),
    )


@router.get("/employees/create", response_class=HTMLResponse)
async def employee_create_get_hr(request: Request, user: str = Depends(get_current_user)):
    if not await _licensed():
        return await _locked(request)
    auth = await _session_user(request)
    if not has_hr_permission(auth.role, "hr.manage_employees"):
        return _no_permission(request)
    return render_template(
        request,
        "hr/employee_form.html",
        _ctx(
            request,
            employee=None,
            departments=await hr.list_departments(active_only=True),
            groups=await hr.list_employee_groups(active_only=True),
            shifts=await hr.list_shifts(active_only=True),
            error="",
        ),
    )


@router.post("/employees/create")
async def employee_create_post_hr(
    request: Request,
    full_name: str = Form(...),
    department_id: str = Form(""),
    group_id: str = Form(""),
    default_shift_id: str = Form(""),
    position: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    employee_code: str = Form(""),
    employment_status: str = Form("active"),
    hire_date: str = Form(""),
    notes: str = Form(""),
    user: str = Depends(get_current_user),
):
    if not await _licensed():
        return await _locked(request)
    auth = await _session_user(request)
    if not has_hr_permission(auth.role, "hr.manage_employees"):
        return _no_permission(request)
    employee = await hr.create_employee(
        full_name=full_name,
        department_id=int(department_id) if department_id else None,
        group_id=int(group_id) if group_id else None,
        default_shift_id=int(default_shift_id) if default_shift_id else None,
        position=position,
        email=email,
        phone=phone,
        employee_code=employee_code,
        employment_status=employment_status,
        hire_date=hire_date,
        notes=notes,
        actor=auth.username,
    )
    return RedirectResponse(url=f"/employees/{employee['id']}", status_code=303)


async def _list_offices_safe() -> list:
    if not await _multi_office_licensed():
        return []
    try:
        from ditaknet.core.hr import offices as office_service

        return await office_service.list_offices()
    except Exception:
        return []


@router.get("/offices", response_class=HTMLResponse)
async def offices_list(request: Request, user: str = Depends(get_current_user)):
    if not await _licensed() or not await _multi_office_licensed():
        return await _locked(request)
    auth = await _session_user(request)
    if not has_office_permission(auth.role, "offices.view"):
        return _no_permission(request)
    from ditaknet.core.hr import offices as office_service

    return render_template(
        request,
        "hr/offices.html",
        _ctx(request, offices=await office_service.list_offices()),
    )


@router.get("/offices/create", response_class=HTMLResponse)
async def office_create_form(request: Request, user: str = Depends(get_current_user)):
    if not await _licensed() or not await _multi_office_licensed():
        return await _locked(request)
    auth = await _session_user(request)
    if not has_office_permission(auth.role, "offices.manage"):
        return _no_permission(request)
    return render_template(request, "hr/office_form.html", _ctx(request, office=None))


@router.post("/offices/create")
async def office_create_post(
    request: Request,
    name: str = Form(...),
    code: str = Form(...),
    timezone: str = Form("UTC"),
    subnet_cidr: str = Form(""),
    address: str = Form(""),
    city: str = Form(""),
    user: str = Depends(get_current_user),
):
    if not await _licensed() or not await _multi_office_licensed():
        return await _locked(request)
    auth = await _session_user(request)
    if not has_office_permission(auth.role, "offices.manage"):
        return _no_permission(request)
    from ditaknet.core.hr import offices as office_service

    office = await office_service.create_office(
        name=name,
        code=code,
        timezone=timezone,
        subnet_cidr=subnet_cidr,
        address=address,
        city=city,
        actor=auth.username,
    )
    token = office.get("branch_token_once", "")
    return render_template(
        request,
        "hr/office_detail.html",
        _ctx(
            request,
            office=office,
            agents=[],
            new_token=token,
            can_manage_tokens=has_office_permission(auth.role, "branches.manage_tokens"),
        ),
    )


@router.get("/offices/{office_id}", response_class=HTMLResponse)
async def office_detail(office_id: int, request: Request, user: str = Depends(get_current_user)):
    if not await _licensed() or not await _multi_office_licensed():
        return await _locked(request)
    auth = await _session_user(request)
    if not has_office_permission(auth.role, "offices.view"):
        return _no_permission(request)
    from ditaknet.core.hr import offices as office_service

    office = await office_service.get_office(office_id)
    if not office:
        raise HTTPException(status_code=404, detail="Office not found")
    return render_template(
        request,
        "hr/office_detail.html",
        _ctx(
            request,
            office=office,
            agents=await office_service.list_office_agents(office_id),
            new_token="",
            can_manage_tokens=has_office_permission(auth.role, "branches.manage_tokens"),
        ),
    )


@router.post("/offices/{office_id}/rotate-token")
async def office_rotate_token(office_id: int, request: Request, user: str = Depends(get_current_user)):
    if not await _licensed() or not await _multi_office_licensed():
        return await _locked(request)
    auth = await _session_user(request)
    if not has_office_permission(auth.role, "branches.manage_tokens"):
        return _no_permission(request)
    from ditaknet.core.hr import offices as office_service

    office = await office_service.rotate_branch_token(office_id, actor=auth.username)
    return render_template(
        request,
        "hr/office_detail.html",
        _ctx(
            request,
            office=office,
            agents=await office_service.list_office_agents(office_id),
            new_token=office.get("branch_token_once", ""),
            can_manage_tokens=True,
        ),
    )
