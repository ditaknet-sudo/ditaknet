"""Employee Attendance & Presence API (/api/hr/*)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from ditaknet.core import hr
from ditaknet.core.hr.access import assert_employee_in_scope, get_user_department_scope
from ditaknet.core.licensing import LicenseLimitError
from ditaknet.security import AuthenticatedUser, has_hr_permission, require_hr_permissions

router = APIRouter(prefix="/hr", tags=["hr-attendance"])


async def require_hr_feature() -> None:
    try:
        await hr.enforce_hr_access()
    except LicenseLimitError as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "feature_not_included",
                "message": "This feature is not included in your current DitakNet package.",
                "required_package": "Corporate",
            },
        ) from exc


async def _scope(user: AuthenticatedUser) -> list[int] | None:
    return await get_user_department_scope(user)


# ─── Schemas ──────────────────────────────────────────────


class DepartmentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    manager_user_id: str = ""
    is_active: bool = True


class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    department_id: int | None = None
    default_shift_id: int | None = None
    is_active: bool = True


class ShiftCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    start_time: str = Field(..., pattern=r"^\d{1,2}:\d{2}$")
    end_time: str = Field(..., pattern=r"^\d{1,2}:\d{2}$")
    timezone: str = "UTC"
    break_minutes: int = 0
    grace_late_minutes: int = 10
    grace_leave_early_minutes: int = 10
    expected_work_minutes: int | None = None
    color: str = ""
    is_overnight: bool = False
    is_active: bool = True


class ShiftAssignmentCreate(BaseModel):
    shift_id: int
    employee_id: int | None = None
    department_id: int | None = None
    group_id: int | None = None
    valid_from: str
    valid_to: str | None = None
    weekday_rules: dict[str, bool] | None = None


class EmployeeCreate(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=255)
    department_id: int | None = None
    group_id: int | None = None
    default_shift_id: int | None = None
    position: str = ""
    email: str = ""
    phone: str = ""
    employee_code: str = ""
    employment_status: str = Field("active", pattern="^(active|inactive|suspended)$")
    hire_date: str = ""
    notes: str = ""
    privacy_notice_accepted: bool = False


class ManualCorrection(BaseModel):
    employee_id: int
    date: str
    worked_minutes: int | None = None
    status: str | None = None
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    note: str = Field(..., min_length=1, max_length=2000)


class ManualCheck(BaseModel):
    employee_id: int
    action: str = Field(..., pattern="^(in|out)$")
    note: str = ""


class AttendanceSettingsUpdate(BaseModel):
    enable_employee_attendance: bool | None = None
    default_shift_id: int | None = None
    presence_online_grace_minutes: int | None = None
    presence_away_after_minutes: int | None = None
    presence_offline_after_minutes: int | None = None
    ignore_gap_minutes: int | None = None
    count_remote_as_work_time: bool | None = None
    require_high_confidence_for_auto_attendance: bool | None = None
    allow_ip_only_attendance: bool | None = None
    allow_manual_corrections: bool | None = None
    export_reports_enabled: bool | None = None
    privacy_notice_required: bool | None = None
    privacy_notice_text: str | None = None


# ─── Departments ──────────────────────────────────────────


@router.get("/departments", dependencies=[Depends(require_hr_feature)])
async def api_list_departments(
    user: AuthenticatedUser = Depends(require_hr_permissions("hr.view")),
):
    return await hr.list_departments()


@router.post("/departments", status_code=201, dependencies=[Depends(require_hr_feature)])
async def api_create_department(
    payload: DepartmentCreate,
    user: AuthenticatedUser = Depends(require_hr_permissions("hr.manage_departments")),
):
    return await hr.create_department(**payload.model_dump(), actor=user.username)


@router.put("/departments/{department_id}", dependencies=[Depends(require_hr_feature)])
async def api_update_department(
    department_id: int,
    payload: DepartmentCreate,
    user: AuthenticatedUser = Depends(require_hr_permissions("hr.manage_departments")),
):
    dept = await hr.update_department(department_id, actor=user.username, **payload.model_dump())
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found")
    return dept


# ─── Groups ───────────────────────────────────────────────


@router.get("/groups", dependencies=[Depends(require_hr_feature)])
async def api_list_groups(
    department_id: int | None = Query(None),
    user: AuthenticatedUser = Depends(require_hr_permissions("hr.view")),
):
    return await hr.list_employee_groups(department_id=department_id)


@router.post("/groups", status_code=201, dependencies=[Depends(require_hr_feature)])
async def api_create_group(
    payload: GroupCreate,
    user: AuthenticatedUser = Depends(require_hr_permissions("hr.manage_groups")),
):
    return await hr.create_employee_group(**payload.model_dump(), actor=user.username)


@router.put("/groups/{group_id}", dependencies=[Depends(require_hr_feature)])
async def api_update_group(
    group_id: int,
    payload: GroupCreate,
    user: AuthenticatedUser = Depends(require_hr_permissions("hr.manage_groups")),
):
    group = await hr.update_employee_group(group_id, actor=user.username, **payload.model_dump())
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


# ─── Shifts ───────────────────────────────────────────────


@router.get("/shifts", dependencies=[Depends(require_hr_feature)])
async def api_list_shifts(user: AuthenticatedUser = Depends(require_hr_permissions("hr.view"))):
    return await hr.list_shifts()


@router.post("/shifts", status_code=201, dependencies=[Depends(require_hr_feature)])
async def api_create_shift(
    payload: ShiftCreate,
    user: AuthenticatedUser = Depends(require_hr_permissions("hr.manage_shifts")),
):
    return await hr.create_shift(**payload.model_dump(), actor=user.username)


@router.put("/shifts/{shift_id}", dependencies=[Depends(require_hr_feature)])
async def api_update_shift(
    shift_id: int,
    payload: ShiftCreate,
    user: AuthenticatedUser = Depends(require_hr_permissions("hr.manage_shifts")),
):
    shift = await hr.update_shift(shift_id, actor=user.username, **payload.model_dump())
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found")
    return shift


@router.post("/shifts/assignments", status_code=201, dependencies=[Depends(require_hr_feature)])
async def api_create_shift_assignment(
    payload: ShiftAssignmentCreate,
    user: AuthenticatedUser = Depends(require_hr_permissions("hr.manage_shifts")),
):
    return await hr.create_shift_assignment(**payload.model_dump(), actor=user.username)


# ─── Employees ────────────────────────────────────────────


@router.get("/employees", dependencies=[Depends(require_hr_feature)])
async def api_list_employees(
    search: str = Query(""),
    department_id: int | None = Query(None),
    group_id: int | None = Query(None),
    employment_status: str = Query(""),
    user: AuthenticatedUser = Depends(require_hr_permissions("hr.view")),
):
    scope = await _scope(user)
    return await hr.list_employees(
        search=search,
        department_id=department_id,
        group_id=group_id,
        employment_status=employment_status,
        department_ids=scope,
    )


@router.post("/employees", status_code=201, dependencies=[Depends(require_hr_feature)])
async def api_create_employee(
    payload: EmployeeCreate,
    user: AuthenticatedUser = Depends(require_hr_permissions("hr.manage_employees")),
):
    return await hr.create_employee(**payload.model_dump(), actor=user.username)


@router.get("/employees/{employee_id}", dependencies=[Depends(require_hr_feature)])
async def api_get_employee(
    employee_id: int,
    user: AuthenticatedUser = Depends(require_hr_permissions("hr.view")),
):
    try:
        await assert_employee_in_scope(employee_id, user)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    employee = await hr.get_employee_with_devices(employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee


@router.put("/employees/{employee_id}", dependencies=[Depends(require_hr_feature)])
async def api_update_employee(
    employee_id: int,
    payload: EmployeeCreate,
    user: AuthenticatedUser = Depends(require_hr_permissions("hr.manage_employees")),
):
    employee = await hr.update_employee(employee_id, actor=user.username, **payload.model_dump())
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee


# ─── Attendance ───────────────────────────────────────────


@router.get("/attendance", dependencies=[Depends(require_hr_feature)])
async def api_list_attendance(
    day: str = Query(""),
    department_id: int | None = Query(None),
    group_id: int | None = Query(None),
    shift_id: int | None = Query(None),
    search: str = Query(""),
    status: str = Query(""),
    confidence: str = Query(""),
    user: AuthenticatedUser = Depends(require_hr_permissions("hr.view_attendance")),
):
    target = date.fromisoformat(day) if day else date.today()
    scope = await _scope(user)
    return await hr.list_attendance_days(
        day=target,
        department_id=department_id,
        group_id=group_id,
        shift_id=shift_id,
        search=search,
        status=status,
        confidence=confidence,
        department_ids=scope,
    )


@router.post("/attendance/refresh", dependencies=[Depends(require_hr_feature)])
async def api_refresh_attendance(
    day: str = Query(""),
    user: AuthenticatedUser = Depends(require_hr_permissions("hr.edit_attendance")),
):
    target = date.fromisoformat(day) if day else None
    return await hr.refresh_attendance_for_date(target)


@router.post("/attendance/manual-check", dependencies=[Depends(require_hr_feature)])
async def api_manual_check(
    payload: ManualCheck,
    user: AuthenticatedUser = Depends(require_hr_permissions("hr.edit_attendance")),
):
    try:
        await assert_employee_in_scope(payload.employee_id, user)
        return await hr.manual_check_in_out(
            employee_id=payload.employee_id,
            action=payload.action,
            note=payload.note,
            actor=user.username,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/attendance/correction", dependencies=[Depends(require_hr_feature)])
async def api_manual_correction(
    payload: ManualCorrection,
    user: AuthenticatedUser = Depends(require_hr_permissions("hr.edit_attendance")),
):
    try:
        await assert_employee_in_scope(payload.employee_id, user)
        return await hr.manual_correction(
            employee_id=payload.employee_id,
            day=date.fromisoformat(payload.date),
            worked_minutes=payload.worked_minutes,
            status=payload.status,
            first_seen_at=payload.first_seen_at,
            last_seen_at=payload.last_seen_at,
            note=payload.note,
            actor=user.username,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/attendance/summary/today", dependencies=[Depends(require_hr_feature)])
async def api_today_summary(
    user: AuthenticatedUser = Depends(require_hr_permissions("hr.view_attendance")),
):
    if not has_hr_permission(user.role, "hr.view_attendance"):
        raise HTTPException(status_code=403, detail="Insufficient HR permissions")
    return await hr.today_attendance_summary()


# ─── Reports ──────────────────────────────────────────────


@router.get("/reports/monthly", dependencies=[Depends(require_hr_feature)])
async def api_monthly_report(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    department_id: int | None = Query(None),
    group_id: int | None = Query(None),
    employee_id: int | None = Query(None),
    office_id: int | None = Query(None),
    status: str = Query(""),
    confidence: str = Query(""),
    user: AuthenticatedUser = Depends(require_hr_permissions("hr.view_attendance")),
):
    scope = await _scope(user)
    if department_id is not None and scope is not None and department_id not in scope:
        raise HTTPException(status_code=403, detail="Department not in scope")
    return await hr.monthly_report(
        month=month,
        department_id=department_id,
        group_id=group_id,
        employee_id=employee_id,
        office_id=office_id,
        status=status,
        confidence=confidence,
        department_ids=scope,
        actor=user.username,
    )


@router.get("/reports/monthly/export.csv", dependencies=[Depends(require_hr_feature)])
async def api_monthly_export(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    department_id: int | None = Query(None),
    group_id: int | None = Query(None),
    employee_id: int | None = Query(None),
    user: AuthenticatedUser = Depends(require_hr_permissions("hr.export_attendance_reports")),
):
    scope = await _scope(user)
    csv_data = await hr.export_monthly_csv(
        month=month,
        department_id=department_id,
        group_id=group_id,
        employee_id=employee_id,
        department_ids=scope,
        actor=user.username,
    )
    return PlainTextResponse(csv_data, media_type="text/csv")


@router.get("/reports/daily/export.csv", dependencies=[Depends(require_hr_feature)])
async def api_daily_export(
    day: str = Query(""),
    user: AuthenticatedUser = Depends(require_hr_permissions("hr.export_attendance_reports")),
):
    from ditaknet.core.hr.reports import export_daily_csv

    target = date.fromisoformat(day) if day else date.today()
    scope = await _scope(user)
    csv_data = await export_daily_csv(day=target, department_ids=scope, actor=user.username)
    return PlainTextResponse(csv_data, media_type="text/csv")


# ─── Settings ─────────────────────────────────────────────


@router.get("/attendance/settings", dependencies=[Depends(require_hr_feature)])
async def api_get_settings(user: AuthenticatedUser = Depends(require_hr_permissions("hr.view"))):
    return await hr.get_attendance_settings()


# ─── Offices / Branches ───────────────────────────────────


class OfficeCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    code: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    address: str = ""
    city: str = ""
    timezone: str = "UTC"
    subnet_cidr: str = ""
    public_ip: str = ""
    status: str = Field("active", pattern="^(active|inactive)$")


class OfficeUpdate(BaseModel):
    name: str | None = None
    address: str | None = None
    city: str | None = None
    timezone: str | None = None
    subnet_cidr: str | None = None
    public_ip: str | None = None
    status: str | None = Field(None, pattern="^(active|inactive)$")


@router.get("/offices", dependencies=[Depends(require_hr_feature)])
async def api_list_offices(
    user: AuthenticatedUser = Depends(require_hr_permissions("offices.view")),
):
    from ditaknet.core.hr import offices as office_service

    return await office_service.list_offices()


@router.post("/offices", dependencies=[Depends(require_hr_feature)])
async def api_create_office(
    body: OfficeCreate,
    user: AuthenticatedUser = Depends(require_hr_permissions("offices.manage")),
):
    from ditaknet.core.hr import offices as office_service

    try:
        office = await office_service.create_office(actor=user.username, **body.model_dump())
    except LicenseLimitError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return office


@router.get("/offices/summary/dashboard", dependencies=[Depends(require_hr_feature)])
async def api_offices_dashboard(
    user: AuthenticatedUser = Depends(require_hr_permissions("offices.view")),
):
    from ditaknet.core.hr import offices as office_service

    return await office_service.offices_dashboard_summary()


@router.get("/offices/{office_id}", dependencies=[Depends(require_hr_feature)])
async def api_get_office(
    office_id: int,
    user: AuthenticatedUser = Depends(require_hr_permissions("offices.view")),
):
    from ditaknet.core.hr import offices as office_service

    office = await office_service.get_office(office_id)
    if not office:
        raise HTTPException(status_code=404, detail="Office not found")
    office["agents"] = await office_service.list_office_agents(office_id)
    return office


@router.put("/offices/{office_id}", dependencies=[Depends(require_hr_feature)])
async def api_update_office(
    office_id: int,
    body: OfficeUpdate,
    user: AuthenticatedUser = Depends(require_hr_permissions("offices.manage")),
):
    from ditaknet.core.hr import offices as office_service

    try:
        return await office_service.update_office(
            office_id, actor=user.username, **body.model_dump(exclude_unset=True)
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/offices/{office_id}/rotate-token", dependencies=[Depends(require_hr_feature)])
async def api_rotate_branch_token(
    office_id: int,
    user: AuthenticatedUser = Depends(require_hr_permissions("branches.manage_tokens")),
):
    from ditaknet.core.hr import offices as office_service

    try:
        return await office_service.rotate_branch_token(office_id, actor=user.username)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/attendance/settings", dependencies=[Depends(require_hr_feature)])
async def api_update_settings(
    payload: AttendanceSettingsUpdate,
    user: AuthenticatedUser = Depends(require_hr_permissions("hr.manage_attendance_settings")),
):
    return await hr.update_attendance_settings(
        actor=user.username,
        **payload.model_dump(exclude_unset=True),
    )
