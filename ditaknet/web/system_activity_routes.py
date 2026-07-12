"""Server Health Dashboard web routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ditaknet.api.system_logs import _serialize_log
from ditaknet.core.health_dashboard import build_health_dashboard
from ditaknet.core.licensing import license_service
from ditaknet.i18n import translate
from ditaknet.security import require_web_permissions, verify_web_csrf
from ditaknet.web.routes import render_template

router = APIRouter(include_in_schema=False, dependencies=[Depends(verify_web_csrf)])

HEALTH_ASSET_BUILD = "20260712a"


def _ctx(request: Request, **extra):
    lang = request.session.get("lang", "en")
    return {
        "lang": lang,
        "t": lambda k, **kw: translate(k, lang, **kw),
        **extra,
    }


async def _dashboard_context(request: Request) -> dict:
    dashboard = await build_health_dashboard()
    events = [_serialize_log(row) for row in dashboard.get("important_events") or []]
    metrics = dashboard.get("metrics") or {}
    compact = dashboard.get("compact") or {}
    return _ctx(
        request,
        dashboard=dashboard,
        compact=compact,
        metrics=metrics,
        preview_events=events,
        active_jobs=dashboard.get("active_jobs") or [],
        running_checks=dashboard.get("running_checks") or [],
        discovery=dashboard.get("discovery") or [],
        health=dashboard.get("health") or {},
        workload=dashboard.get("workload") or {},
        overall_status=dashboard.get("overall_status") or "healthy",
        asset_build=HEALTH_ASSET_BUILD,
        feature_flags=dashboard.get("feature_flags") or {},
        license=await license_service.status(),
    )


@router.get("/system/activity", response_class=HTMLResponse)
async def server_health_page(
    request: Request,
    _user: str = Depends(require_web_permissions("system.activity.view")),
):
    return render_template(
        request,
        "system/activity.html",
        await _dashboard_context(request),
    )
