"""
DitakNet — Dashboard API endpoints.

Provides aggregated views of system health for dashboard consumption.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ditaknet import database as db
from ditaknet.models import DashboardSummary, HostStatus
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
async def get_summary(
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    """Get a high-level summary of all hosts, services, and states."""
    stats = await db.get_dashboard_stats()
    alerts = await db.get_recent_alerts(limit=10)
    return {**stats, "recent_alerts": alerts}


@router.get("/operations")
async def get_operations_dashboard(
    lang: str = "en",
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    """Remote-first dashboard: problems, suggestions, license, discovery."""
    from ditaknet.assistant.recommendations import suggested_dashboard_actions
    from ditaknet.core.licensing import license_service

    enhanced = await db.get_enhanced_dashboard()
    enhanced["license"] = await license_service.status()
    enhanced["suggested_actions"] = await suggested_dashboard_actions(lang)
    return enhanced


@router.get("/status", response_model=list[HostStatus])
async def get_status(
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    """Get per-host status with all child services and overall state."""
    return await db.get_hosts_status()
