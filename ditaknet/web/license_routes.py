"""Complimentary Professional status page and legacy redirects."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ditaknet.core.licensing import license_service
from ditaknet.i18n import translate
from ditaknet.security import require_web_permissions, verify_web_csrf
from ditaknet.web.routes import render_template

router = APIRouter(include_in_schema=False, dependencies=[Depends(verify_web_csrf)])


def _ctx(request: Request, **extra):
    lang = request.session.get("lang", "en")
    base = {"lang": lang, "t": lambda key, **kw: translate(key, lang, **kw)}
    base.update(extra)
    return base


@router.get("/license", response_class=HTMLResponse)
async def license_page(
    request: Request,
    user: str = Depends(require_web_permissions("license.view")),
):
    return render_template(
        request,
        "license/index.html",
        _ctx(
            request,
            active="license",
            license=await license_service.status(),
        ),
    )


@router.get("/settings/license", response_class=HTMLResponse)
@router.get("/license/required", response_class=HTMLResponse)
async def legacy_license_redirect(request: Request):
    return RedirectResponse(url="/license", status_code=303)


@router.get("/purchase", response_class=HTMLResponse)
@router.get("/purchase/checkout", response_class=HTMLResponse)
@router.get("/settings/license/purchase", response_class=HTMLResponse)
@router.get("/settings/license/purchases", response_class=HTMLResponse)
async def retired_sales_redirect(request: Request):
    return RedirectResponse(url="/license", status_code=303)
