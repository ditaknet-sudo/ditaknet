"""
Hosts API — monitored machines (address, tags, location metadata).

Each host owns many services (individual checks). Deleting a host cascades
to its services in SQLite via FK ``ON DELETE CASCADE``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ditaknet import database as db
from ditaknet.core.licensing import LicenseLimitError, license_service
from ditaknet.models import Host, HostCreate, HostUpdate
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/hosts", tags=["hosts"])


@router.get("", response_model=list[Host])
async def list_hosts(
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    """List all monitored hosts."""
    return await db.list_hosts()


@router.get("/{host_id}", response_model=Host)
async def get_host(
    host_id: int,
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    """Get a single host by ID."""
    host = await db.get_host(host_id)
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    return host


@router.post("", response_model=Host, status_code=201)
async def create_host(
    payload: HostCreate,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    """Create a new monitored host."""
    try:
        await license_service.enforce_host_create(address=payload.address)
    except LicenseLimitError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return await db.create_host(
        name=payload.name,
        address=payload.address,
        host_type=payload.host_type,
        location=payload.location,
        tags=payload.tags,
        enabled=payload.enabled,
    )


@router.put("/{host_id}", response_model=Host)
async def update_host(
    host_id: int,
    payload: HostUpdate,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    """Update an existing host."""
    existing = await db.get_host(host_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Host not found")
    if payload.address:
        try:
            await license_service.enforce_host_network_scope(payload.address)
        except LicenseLimitError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    updated = await db.update_host(
        host_id,
        name=payload.name,
        address=payload.address,
        host_type=payload.host_type,
        location=payload.location,
        tags=payload.tags,
        enabled=payload.enabled,
    )
    return updated


@router.delete("/{host_id}", status_code=204)
async def delete_host(
    host_id: int,
    user: AuthenticatedUser = Depends(require_permissions("admin")),
):
    """Delete a host and all its services (cascade)."""
    deleted = await db.delete_host(host_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Host not found")
