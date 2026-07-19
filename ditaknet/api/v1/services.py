"""
Services API — one monitored check target per row (HTTP/TCP/ping on a host).

``run-now`` / ``check`` bypass the scheduler interval and invoke the same
``Scheduler._execute_once`` path so manual runs update state and alerts identically.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ditaknet import database as db
from ditaknet.api.deps import get_scheduler
from ditaknet.core.licensing import LicenseLimitError, license_service
from ditaknet.models import CheckResult, Service, ServiceCreate, ServiceUpdate
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/services", tags=["services"])


@router.get("", response_model=list[Service])
async def list_services(
    host_id: Optional[int] = Query(None),
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    """List all services, optionally filtered by host."""
    return await db.list_services(host_id=host_id)


@router.get("/{service_id}", response_model=Service)
async def get_service(
    service_id: int,
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    """Get a single service by ID."""
    svc = await db.get_service(service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")
    return svc


@router.post("", response_model=Service, status_code=201)
async def create_service(
    payload: ServiceCreate,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    """Create a new monitored service.

    The service is automatically scheduled for periodic checks.
    """
    # Validate host exists
    host = await db.get_host(payload.host_id)
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    try:
        await license_service.enforce_service_create()
    except LicenseLimitError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    svc = await db.create_service(
        host_id=payload.host_id,
        name=payload.name,
        check_type=payload.check_type.value,
        target=payload.target,
        port=payload.port,
        interval_seconds=payload.interval_seconds,
        timeout_seconds=payload.timeout_seconds,
        expected_status_code=payload.expected_status_code,
        retry_count=payload.retry_count,
        max_attempts=payload.max_attempts,
        enabled=payload.enabled,
    )

    # Schedule the check
    if svc.get("enabled"):
        scheduler = get_scheduler()
        scheduler.add_service(svc)

    return svc


@router.put("/{service_id}", response_model=Service)
async def update_service(
    service_id: int,
    payload: ServiceUpdate,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    """Update an existing service.

    If the interval or enabled state changes, the scheduled job is updated.
    """
    existing = await db.get_service(service_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Service not found")

    update_fields = payload.model_dump(exclude_unset=True)
    if "check_type" in update_fields and update_fields["check_type"] is not None:
        update_fields["check_type"] = update_fields["check_type"].value

    updated = await db.update_service(service_id, **update_fields)

    # Reschedule if needed
    scheduler = get_scheduler()
    scheduler.reschedule_service(updated)

    return updated


@router.delete("/{service_id}", status_code=204)
async def delete_service(
    service_id: int,
    user: AuthenticatedUser = Depends(require_permissions("admin")),
):
    """Delete a service and remove its scheduled job."""
    existing = await db.get_service(service_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Service not found")

    # Remove from scheduler
    scheduler = get_scheduler()
    scheduler.remove_service(service_id)

    await db.delete_service(service_id)


async def _run_service_check_now(service_id: int) -> dict:
    """Execute an immediate check for the given service."""
    svc = await db.get_service(service_id)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found")

    scheduler = get_scheduler()
    result = await scheduler.trigger_check(service_id)
    if not result:
        raise HTTPException(status_code=500, detail="Check execution failed")
    result.setdefault("service_id", service_id)
    return result


@router.post("/{service_id}/check", response_model=dict)
async def trigger_check(
    service_id: int,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    """Trigger an immediate check for a specific service."""
    return await _run_service_check_now(service_id)


@router.post("/{service_id}/run-now", response_model=CheckResult)
async def run_service_now(
    service_id: int,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    """Run the selected service check immediately.

    Executes the check, persists the result, updates service state
    via the state engine, and triggers alert logic on transitions.
    """
    return await _run_service_check_now(service_id)
