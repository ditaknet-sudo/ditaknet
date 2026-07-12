"""Appearance / theme settings page."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ditaknet.i18n import translate
from ditaknet.security import require_web_permissions, verify_web_csrf
from ditaknet.web.routes import render_template

router = APIRouter(include_in_schema=False, dependencies=[Depends(verify_web_csrf)])

APPEARANCE_ASSET_BUILD = "20260711e"


@router.get("/settings/appearance", response_class=HTMLResponse)
async def settings_appearance_page(
    request: Request,
    _user: str = Depends(require_web_permissions("settings.view")),
):
    lang = request.session.get("lang", "en")
    return render_template(
        request,
        "settings/appearance.html",
        {
            "lang": lang,
            "active": "appearance",
            "asset_build": APPEARANCE_ASSET_BUILD,
            "t": lambda k, **kw: translate(k, lang, **kw),
        },
    )
