"""System logs API."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse

from ditaknet import database as db
from ditaknet.api.v1.system import _scheduler_payload
from ditaknet.core.health_dashboard import _health_summary_base
from ditaknet.core.system_log_service import redact_text, uptime_seconds
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/system/logs", tags=["system-logs"])


def _serialize_log(row: dict) -> dict:
    meta = {}
    try:
        meta = json.loads(row.get("metadata_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        meta = {}
    return {
        "id": row.get("id"),
        "created_at": row.get("created_at"),
        "level": row.get("level"),
        "category": row.get("category"),
        "event_type": row.get("event_type"),
        "message": redact_text(str(row.get("message") or "")),
        "source": row.get("source"),
        "entity_type": row.get("entity_type"),
        "entity_id": row.get("entity_id"),
        "user_id": row.get("user_id"),
        "ip_address": row.get("ip_address"),
        "metadata": meta,
        "is_sensitive": bool(row.get("is_sensitive")),
    }


@router.get("/list")
async def list_logs(
    category: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    errors_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: AuthenticatedUser = Depends(require_permissions("system.logs.view")),
) -> dict:
    logs = await db.list_system_logs(
        category=category,
        level=level,
        search=search,
        date_from=date_from,
        date_to=date_to,
        errors_only=errors_only,
        limit=limit,
        offset=offset,
    )
    total = await db.count_system_logs_filtered(
        category=category,
        level=level,
        search=search,
        date_from=date_from,
        date_to=date_to,
        errors_only=errors_only,
    )
    return {
        "logs": [_serialize_log(row) for row in logs],
        "limit": limit,
        "offset": offset,
        "total": total,
    }


@router.get("/live")
async def live_logs(
    since_id: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    user: AuthenticatedUser = Depends(require_permissions("system.logs.view")),
) -> dict:
    logs = await db.list_system_logs(since_id=since_id, limit=limit)
    return {"logs": [_serialize_log(row) for row in logs]}


@router.get("/summary")
async def logs_summary(
    user: AuthenticatedUser = Depends(require_permissions("system.logs.view")),
) -> dict:
    scheduler = await _scheduler_payload()
    last_error = await db.get_last_system_log(levels=["error", "critical"])
    last_event = await db.get_last_system_log()
    health = await _health_summary_base()
    return {
        "errors_last_24h": await db.count_system_logs_since(hours=24, levels=["error", "critical"]),
        "warnings_last_24h": await db.count_system_logs_since(hours=24, level="warning"),
        "monitoring_events_last_24h": await db.count_system_logs_since(hours=24, category="monitoring"),
        "failed_checks_last_24h": await db.count_system_logs_since(hours=24, event_type="check_failed"),
        "notifications_failed_last_24h": await db.count_system_logs_since(
            hours=24, category="notification", event_type="notification_failed"
        ),
        "discovery_scans_last_24h": await db.count_system_logs_since(hours=24, event_type="scan_started"),
        "checks_run_today": await db.count_check_results_since(hours=24),
        "failed_checks_today": await db.count_failed_checks_since(hours=24),
        "last_error": _serialize_log(last_error) if last_error else None,
        "last_event": _serialize_log(last_event) if last_event else None,
        "scheduler_status": "running" if scheduler.get("running") else "stopped",
        "app_uptime": uptime_seconds(),
        "health": health,
    }


@router.get("/recent")
async def recent_logs(
    limit: int = Query(50, ge=1, le=200),
    user: AuthenticatedUser = Depends(require_permissions("system.logs.view")),
) -> dict:
    logs = await db.list_system_logs(limit=limit, offset=0)
    return {"logs": [_serialize_log(row) for row in logs]}


@router.get("/export")
async def export_logs(
    format: str = Query("csv", pattern="^(csv|json)$"),
    category: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    user: AuthenticatedUser = Depends(require_permissions("admin")),
) -> Response:
    logs = await db.list_system_logs(
        category=category,
        level=level,
        search=search,
        limit=500,
        offset=0,
    )
    serialized = [_serialize_log(row) for row in logs]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    if format == "json":
        payload = {
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "filters": {"category": category, "level": level, "search": search},
            "logs": serialized,
        }
        body = json.dumps(payload, indent=2, default=str)
        return Response(
            content=body,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="ditaknet-logs-{stamp}.json"'},
        )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["time", "level", "category", "event_type", "message", "source", "user", "entity_type", "entity_id"]
    )
    for row in serialized:
        writer.writerow(
            [
                row.get("created_at"),
                row.get("level"),
                row.get("category"),
                row.get("event_type"),
                redact_text(str(row.get("message") or "")),
                row.get("source"),
                row.get("user_id"),
                row.get("entity_type"),
                row.get("entity_id"),
            ]
        )
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="ditaknet-logs-{stamp}.csv"'},
    )


@router.get("/{log_id}")
async def get_log_detail(
    log_id: int,
    user: AuthenticatedUser = Depends(require_permissions("system.logs.view")),
) -> dict:
    row = await db.get_system_log(log_id)
    if not row:
        raise HTTPException(status_code=404, detail="Log entry not found")
    return {"log": _serialize_log(row)}


health_router = APIRouter(prefix="/system/health", tags=["system-health-legacy"])


@health_router.get("/summary")
async def health_summary_endpoint(
    user: AuthenticatedUser = Depends(require_permissions("system.logs.view")),
) -> dict:
    return await _health_summary_base()
