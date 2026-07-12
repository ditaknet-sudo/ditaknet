"""Employee Presence Monitoring API.

Corporate-only, privacy-aware presence visibility based on approved devices and
network/heartbeat signals.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ditaknet.core import employee_presence as presence
from ditaknet.core.licensing import LicenseLimitError
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(tags=["employee-presence"])


async def require_employee_presence_feature() -> None:
    try:
        await presence.enforce_employee_presence_access()
    except LicenseLimitError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


class EmployeeCreate(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=255)
    department: str = Field("", max_length=255)
    position: str = Field("", max_length=255)
    email: str = Field("", max_length=255)
    phone: str = Field("", max_length=80)
    employee_code: str = Field("", max_length=80)
    status: str = Field("active", pattern="^(active|inactive)$")
    privacy_notice_accepted: bool = False


class EmployeeUpdate(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=255)
    department: str | None = Field(None, max_length=255)
    position: str | None = Field(None, max_length=255)
    email: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=80)
    employee_code: str | None = Field(None, max_length=80)
    status: str | None = Field(None, pattern="^(active|inactive)$")
    privacy_notice_accepted: bool | None = None


class EmployeeDeviceCreate(BaseModel):
    device_name: str = Field(..., min_length=1, max_length=255)
    device_type: str = Field("laptop", pattern="^(laptop|desktop|phone|tablet|other)$")
    mac_address: str = Field("", max_length=80)
    hostname: str = Field("", max_length=255)
    static_ip: str = Field("", max_length=80)
    last_ip: str = Field("", max_length=80)
    agent_id: str = Field("", max_length=80)
    is_primary: bool = False
    is_approved: bool = True


class EmployeeDeviceUpdate(BaseModel):
    device_name: str | None = Field(None, min_length=1, max_length=255)
    device_type: str | None = Field(None, pattern="^(laptop|desktop|phone|tablet|other)$")
    mac_address: str | None = Field(None, max_length=80)
    hostname: str | None = Field(None, max_length=255)
    static_ip: str | None = Field(None, max_length=80)
    last_ip: str | None = Field(None, max_length=80)
    agent_id: str | None = Field(None, max_length=80)
    is_primary: bool | None = None
    is_approved: bool | None = None


class LinkDiscoveredDevice(BaseModel):
    employee_id: int
    device_type: str = Field("laptop", pattern="^(laptop|desktop|phone|tablet|other)$")
    is_primary: bool = False


class ManualStatusUpdate(BaseModel):
    employee_id: int
    status: str = Field(..., pattern="^(onsite|remote|away|offline|unknown)$")
    connection_type: str = Field(
        "manual", pattern="^(onsite_wifi|onsite_lan|remote_agent|vpn|manual|unknown)$"
    )
    confidence: str = Field("medium", pattern="^(high|medium|low)$")
    notes: str = Field("", max_length=1000)


class PresenceSettingsUpdate(BaseModel):
    configured_enabled: bool = False
    presence_online_grace_minutes: int = Field(5, ge=1, le=240)
    presence_away_after_minutes: int = Field(15, ge=1, le=1440)
    presence_offline_after_minutes: int = Field(60, ge=1, le=10080)
    allowed_detection_sources: list[str] = Field(
        default_factory=lambda: [
            "arp_scan",
            "ping_check",
            "dhcp_lease",
            "agent_heartbeat",
            "vpn_heartbeat",
            "manual_update",
        ]
    )
    privacy_notice_text: str = Field(presence.DEFAULT_PRIVACY_NOTICE, max_length=4000)


@router.get("/employees", dependencies=[Depends(require_employee_presence_feature)])
async def list_employees(
    search: str = Query(""),
    department: str = Query(""),
    status: str = Query(""),
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    return await presence.list_employees(search=search, department=department, status=status)


@router.post(
    "/employees", status_code=201, dependencies=[Depends(require_employee_presence_feature)]
)
async def create_employee(
    payload: EmployeeCreate,
    user: AuthenticatedUser = Depends(require_permissions("admin")),
):
    return await presence.create_employee(**payload.model_dump(), actor=user.username)


@router.get("/employees/{employee_id}", dependencies=[Depends(require_employee_presence_feature)])
async def get_employee(
    employee_id: int,
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    employee = await presence.get_employee(employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee


@router.put("/employees/{employee_id}", dependencies=[Depends(require_employee_presence_feature)])
async def update_employee(
    employee_id: int,
    payload: EmployeeUpdate,
    user: AuthenticatedUser = Depends(require_permissions("admin")),
):
    employee = await presence.update_employee(
        employee_id,
        actor=user.username,
        **payload.model_dump(exclude_unset=True),
    )
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee


@router.delete(
    "/employees/{employee_id}", dependencies=[Depends(require_employee_presence_feature)]
)
async def deactivate_employee(
    employee_id: int,
    user: AuthenticatedUser = Depends(require_permissions("admin")),
):
    ok = await presence.deactivate_employee(employee_id, actor=user.username)
    if not ok:
        raise HTTPException(status_code=404, detail="Employee not found")
    return {"deactivated": True}


@router.get(
    "/employees/{employee_id}/devices", dependencies=[Depends(require_employee_presence_feature)]
)
async def list_employee_devices(
    employee_id: int,
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    if not await presence.get_employee(employee_id):
        raise HTTPException(status_code=404, detail="Employee not found")
    return await presence.list_employee_devices(employee_id)


@router.post(
    "/employees/{employee_id}/devices",
    status_code=201,
    dependencies=[Depends(require_employee_presence_feature)],
)
async def create_employee_device(
    employee_id: int,
    payload: EmployeeDeviceCreate,
    user: AuthenticatedUser = Depends(require_permissions("admin")),
):
    try:
        return await presence.create_employee_device(
            employee_id=employee_id,
            **payload.model_dump(),
            actor=user.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put(
    "/employee-devices/{device_id}", dependencies=[Depends(require_employee_presence_feature)]
)
async def update_employee_device(
    device_id: int,
    payload: EmployeeDeviceUpdate,
    user: AuthenticatedUser = Depends(require_permissions("admin")),
):
    device = await presence.update_employee_device(
        device_id,
        actor=user.username,
        **payload.model_dump(exclude_unset=True),
    )
    if not device:
        raise HTTPException(status_code=404, detail="Employee device not found")
    return device


@router.delete(
    "/employee-devices/{device_id}", dependencies=[Depends(require_employee_presence_feature)]
)
async def delete_employee_device(
    device_id: int,
    user: AuthenticatedUser = Depends(require_permissions("admin")),
):
    ok = await presence.delete_employee_device(device_id, actor=user.username)
    if not ok:
        raise HTTPException(status_code=404, detail="Employee device not found")
    return {"deleted": True}


@router.post(
    "/employee-devices/{discovered_device_id}/link-discovered-device",
    dependencies=[Depends(require_employee_presence_feature)],
)
async def link_discovered_device(
    discovered_device_id: int,
    payload: LinkDiscoveredDevice,
    user: AuthenticatedUser = Depends(require_permissions("admin")),
):
    try:
        return await presence.link_discovered_device_to_employee(
            employee_id=payload.employee_id,
            discovered_device_id=discovered_device_id,
            device_type=payload.device_type,
            is_primary=payload.is_primary,
            actor=user.username,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/employee-presence", dependencies=[Depends(require_employee_presence_feature)])
async def list_employee_presence(
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    return await presence.list_employee_presence()


@router.get("/employee-presence/summary", dependencies=[Depends(require_employee_presence_feature)])
async def employee_presence_summary(
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    return await presence.summarize_presence()


@router.get(
    "/employees/{employee_id}/presence", dependencies=[Depends(require_employee_presence_feature)]
)
async def employee_presence_detail(
    employee_id: int,
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    if not await presence.get_employee(employee_id):
        raise HTTPException(status_code=404, detail="Employee not found")
    return {
        "presence": await presence.get_employee_presence(employee_id),
        "events": await presence.list_presence_events(employee_id=employee_id, limit=100),
    }


@router.post(
    "/employee-presence/refresh", dependencies=[Depends(require_employee_presence_feature)]
)
async def refresh_employee_presence(
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    return await presence.refresh_presence()


@router.post(
    "/employee-presence/manual-status", dependencies=[Depends(require_employee_presence_feature)]
)
async def manual_status_update(
    payload: ManualStatusUpdate,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    try:
        return await presence.manual_status_update(**payload.model_dump(), actor=user.username)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/employee-presence/reports/daily", dependencies=[Depends(require_employee_presence_feature)]
)
async def employee_presence_daily_report(
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    await presence.create_privacy_audit_log(
        actor_user_id=user.username, action="presence_report_viewed", details="daily"
    )
    return await presence.report_daily()


@router.get(
    "/employee-presence/reports/range", dependencies=[Depends(require_employee_presence_feature)]
)
async def employee_presence_range_report(
    start: str = Query(""),
    end: str = Query(""),
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    await presence.create_privacy_audit_log(
        actor_user_id=user.username, action="presence_report_viewed", details=f"{start}:{end}"
    )
    return await presence.report_range(start=start, end=end)


@router.get(
    "/settings/employee-presence", dependencies=[Depends(require_employee_presence_feature)]
)
async def get_employee_presence_settings(
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    return await presence.get_presence_settings()


@router.put(
    "/settings/employee-presence", dependencies=[Depends(require_employee_presence_feature)]
)
async def update_employee_presence_settings(
    payload: PresenceSettingsUpdate,
    user: AuthenticatedUser = Depends(require_permissions("admin")),
):
    return await presence.update_presence_settings(**payload.model_dump(), actor=user.username)
