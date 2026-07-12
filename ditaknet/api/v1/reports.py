"""Reporting and export API endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from ditaknet import database as db
from ditaknet.core.licensing import LicenseLimitError, license_service
from ditaknet.core.reports import calculate_summary, checks_to_csv, service_breakdown
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/summary")
async def summary_report(
    service_id: Optional[int] = Query(None),
    host_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    user: AuthenticatedUser = Depends(require_permissions("read")),
) -> dict:
    try:
        await license_service.enforce_reports_access(export=False)
    except LicenseLimitError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    rows = await db.list_report_check_rows(
        service_id=service_id,
        host_id=host_id,
        status=status,
    )
    return {
        "filters": {
            "service_id": service_id,
            "host_id": host_id,
            "status": status,
        },
        "summary": calculate_summary(rows),
        "services": service_breakdown(rows),
    }


@router.get("/checks.csv")
async def checks_csv_export(
    service_id: Optional[int] = Query(None),
    host_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    user: AuthenticatedUser = Depends(require_permissions("read")),
) -> Response:
    try:
        await license_service.enforce_reports_access(export=True)
    except LicenseLimitError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    rows = await db.list_report_check_rows(
        service_id=service_id,
        host_id=host_id,
        status=status,
    )
    csv_body = checks_to_csv(rows)
    return Response(
        content=csv_body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="checks.csv"'},
    )
