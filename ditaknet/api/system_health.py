"""System health dashboard and compact activity API."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from ditaknet import database as db
from ditaknet.api.system_logs import _serialize_log
from ditaknet.core.activity_service import activity_service
from ditaknet.core.health_dashboard import _discovery_live, build_health_dashboard
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/system", tags=["system-health"])


@router.get("/health-dashboard")
async def health_dashboard(
    user: AuthenticatedUser = Depends(require_permissions("system.activity.view")),
) -> dict:
    payload = await build_health_dashboard()
    compact = dict(payload.get("compact") or {})
    if compact.get("last_error"):
        compact["last_error"] = _serialize_log(compact["last_error"])
    if compact.get("last_warning"):
        compact["last_warning"] = _serialize_log(compact["last_warning"])
    return {
        **compact,
        "active_jobs": payload.get("active_jobs") or [],
        "running_checks": payload.get("running_checks") or [],
        "discovery": payload.get("discovery") or [],
        "important_events": [_serialize_log(row) for row in payload.get("important_events") or []],
        "workload": payload.get("workload") or {},
        "health": payload.get("health") or {},
        "metrics": payload.get("metrics") or {},
        "scheduler": payload.get("scheduler") or {},
        "errors_last_24h": payload.get("errors_last_24h", 0),
        "warnings_last_24h": payload.get("warnings_last_24h", 0),
    }


@router.get("/activity/live")
async def activity_live_compact(
    since_id: int = Query(0, ge=0),
    user: AuthenticatedUser = Depends(require_permissions("system.activity.view")),
) -> dict:
    summary = await activity_service.get_summary()
    discovery = await _discovery_live()
    events = await db.list_system_logs(limit=50, offset=0)
    new_events = await db.list_system_logs(since_id=since_id, limit=50) if since_id > 0 else []
    return {
        "summary": summary,
        "active_jobs": activity_service.get_active_jobs(),
        "discovery": discovery,
        "events": [_serialize_log(row) for row in events],
        "new_events": [_serialize_log(row) for row in new_events],
        "server_status": _server_status_badge(summary),
    }


@router.get("/logs")
async def health_dashboard_logs(
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    errors_only: bool = Query(False),
    user: AuthenticatedUser = Depends(require_permissions("system.logs.view")),
) -> dict:
    logs = await db.list_system_logs(errors_only=errors_only, limit=limit, offset=offset)
    return {
        "logs": [_serialize_log(row) for row in logs],
        "limit": limit,
        "offset": offset,
    }


def _server_status_badge(summary: dict) -> str:
    if summary.get("database_status") != "connected":
        return "error"
    if summary.get("errors_last_24h", 0) > 0 and summary.get("discovery_running", 0) == 0:
        if summary.get("checks_running", 0) == 0:
            return "warning"
    if summary.get("active_jobs_count", 0) > 0 or summary.get("discovery_running", 0) > 0:
        return "busy"
    if summary.get("errors_last_24h", 0) > 5:
        return "warning"
    return "healthy"


activity_router = APIRouter(prefix="/system/activity", tags=["system-activity"])


@activity_router.get("/summary")
async def activity_summary(
    user: AuthenticatedUser = Depends(require_permissions("system.activity.view")),
) -> dict:
    from ditaknet.core.health_dashboard import _health_summary_base

    summary = await activity_service.get_summary()
    return {**summary, "health": await _health_summary_base()}


@activity_router.get("/jobs")
async def activity_jobs(
    user: AuthenticatedUser = Depends(require_permissions("system.activity.view")),
) -> dict:
    jobs = activity_service.get_active_jobs()
    return {"jobs": jobs, "count": len(jobs)}


@activity_router.get("/events")
async def activity_events(
    category: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=200),
    user: AuthenticatedUser = Depends(require_permissions("system.activity.view")),
) -> dict:
    rows = await db.list_system_logs(category=category, level=level, limit=limit, offset=0)
    return {"events": [_serialize_log(row) for row in rows]}


@activity_router.get("/live")
async def activity_live(
    since_id: int = Query(0, ge=0),
    user: AuthenticatedUser = Depends(require_permissions("system.activity.view")),
) -> dict:
    summary = await activity_service.get_summary()
    jobs = activity_service.get_active_jobs()
    discovery = await _discovery_live()
    events = await db.list_system_logs(limit=50, offset=0)
    new_events = await db.list_system_logs(since_id=since_id, limit=50) if since_id > 0 else []
    errors = await db.list_system_logs(errors_only=True, limit=10, offset=0)
    recent_checks = await db.list_system_logs(category="monitoring", limit=15, offset=0)
    return {
        "summary": summary,
        "active_jobs": jobs,
        "discovery": discovery,
        "events": [_serialize_log(row) for row in events],
        "new_events": [_serialize_log(row) for row in new_events],
        "recent_checks": [_serialize_log(row) for row in recent_checks],
        "errors": [_serialize_log(row) for row in errors],
        "server_status": _server_status_badge(summary),
    }


@activity_router.get("/errors")
async def activity_errors(
    limit: int = Query(20, ge=1, le=100),
    user: AuthenticatedUser = Depends(require_permissions("system.activity.view")),
) -> dict:
    logs = await db.list_system_logs(errors_only=True, limit=limit, offset=0)
    return {
        "errors": [_serialize_log(row) for row in logs],
        "errors_last_24h": await db.count_system_logs_since(hours=24, levels=["error", "critical"]),
        "warnings_last_24h": await db.count_system_logs_since(hours=24, level="warning"),
    }
