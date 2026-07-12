"""Branch agent API — registration (admin), heartbeat, presence events."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from ditaknet.core.hr import branches, offices
from ditaknet.core.licensing import LicenseLimitError
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/branches", tags=["branches"])


class BranchRegisterRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    code: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    timezone: str = "UTC"
    subnet_cidr: str = ""
    address: str = ""
    city: str = ""


class BranchHeartbeatRequest(BaseModel):
    agent_version: str = ""
    hostname: str = ""
    local_subnet: str = ""
    scan_status: str = "idle"


class PresenceEventItem(BaseModel):
    office_code: str
    detected_at: str
    mac_address: str = ""
    hostname: str = ""
    ip_address: str = ""
    source: str = "branch_agent"
    confidence: str = "low"
    device_fingerprint: str = ""
    agent_version: str = ""


class PresenceEventsRequest(BaseModel):
    events: list[PresenceEventItem] = Field(default_factory=list)


async def _branch_office(
    x_branch_token: str | None = Header(default=None, alias="X-Branch-Token"),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    token = (x_branch_token or "").strip()
    if not token and authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Branch token required")
    try:
        return await branches.authenticate_branch_token(token)
    except LicenseLimitError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/register")
async def register_branch(
    body: BranchRegisterRequest,
    user: AuthenticatedUser = Depends(require_permissions("admin")),
):
    try:
        office = await branches.register_branch(
            name=body.name,
            code=body.code,
            timezone=body.timezone,
            subnet_cidr=body.subnet_cidr,
            address=body.address,
            city=body.city,
            actor=user.username,
        )
    except LicenseLimitError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "office": {k: v for k, v in office.items() if k != "branch_token_hash"},
        "branch_token": office.get("branch_token_once"),
        "message": "Store this branch token securely. It will not be shown again.",
    }


@router.post("/heartbeat")
async def branch_heartbeat(
    body: BranchHeartbeatRequest,
    office: dict = Depends(_branch_office),
):
    try:
        agent = await branches.record_branch_heartbeat(
            office,
            agent_version=body.agent_version,
            hostname=body.hostname,
            local_subnet=body.local_subnet,
            scan_status=body.scan_status,
        )
    except LicenseLimitError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"ok": True, "office_id": office["id"], "branch_agent_id": agent.get("id")}


@router.post("/presence-events")
async def branch_presence_events(
    body: PresenceEventsRequest,
    office: dict = Depends(_branch_office),
):
    agent_id: int | None = None
    if body.events and body.events[0].agent_version:
        agents = await offices.list_office_agents(int(office["id"]))
        if agents:
            agent_id = int(agents[0]["id"])
    try:
        result = await branches.ingest_presence_events_batch(
            office,
            [e.model_dump() for e in body.events],
            branch_agent_id=agent_id,
        )
    except LicenseLimitError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result
