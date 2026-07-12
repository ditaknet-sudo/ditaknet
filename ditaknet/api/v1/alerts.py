"""
DitakNet — Alerts API endpoints.

Alert history and acknowledgement.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ditaknet import database as db
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[dict])
async def list_alerts(
    service_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    """Get alert history, optionally filtered by service."""
    return await db.list_alerts(
        service_id=service_id,
        limit=limit,
        offset=offset,
    )


@router.post("/{alert_id}/acknowledge", response_model=dict)
async def acknowledge_alert(
    alert_id: int,
    user: AuthenticatedUser = Depends(require_permissions("operate")),
):
    """Acknowledge (resolve) an alert."""
    alert = await db.get_alert(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return await db.acknowledge_alert(alert_id)
