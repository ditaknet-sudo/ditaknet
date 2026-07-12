"""
DitakNet — Check Results API endpoints.

Read-only endpoints for check result history.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from ditaknet import database as db
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/checks", tags=["checks"])


@router.get("", response_model=list[dict])
async def list_checks(
    service_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    """Get check result history, optionally filtered by service."""
    return await db.list_check_results(
        service_id=service_id,
        limit=limit,
        offset=offset,
    )


@router.get("/latest", response_model=list[dict])
async def get_latest_checks(
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    """Get the most recent check result for every service."""
    return await db.get_latest_checks_all()
