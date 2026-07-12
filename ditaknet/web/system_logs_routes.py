"""Full system logs page."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ditaknet.i18n import translate
from ditaknet.security import require_web_permissions, verify_web_csrf
from ditaknet.web.routes import render_template

router = APIRouter(include_in_schema=False, dependencies=[Depends(verify_web_csrf)])

LOGS_ASSET_BUILD = "20260705a"


@router.get("/system/logs", response_class=HTMLResponse)
async def system_logs_page(
    request: Request,
    _user: str = Depends(require_web_permissions("system.logs.view")),
):
    lang = request.session.get("lang", "en")
    return render_template(
        request,
        "system/logs.html",
        {
            "lang": lang,
            "t": lambda k, **kw: translate(k, lang, **kw),
            "asset_build": LOGS_ASSET_BUILD,
        },
    )
