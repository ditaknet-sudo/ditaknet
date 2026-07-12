"""About and Support pages — public-safe product and contact information."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ditaknet.core.system_about import build_about_payload
from ditaknet.i18n import translate
from ditaknet.web.routes import render_template

router = APIRouter(include_in_schema=False)


def _ctx(request: Request, **extra):
    lang = request.session.get("lang", "en")
    base = {"lang": lang, "t": lambda k, **kw: translate(k, lang, **kw)}
    base.update(extra)
    return base


async def _about_context(request: Request, tab: str = "about"):
    lang = request.session.get("lang", "en")
    info = await build_about_payload(lang=lang, request=request)
    user = request.session.get("user")
    return _ctx(
        request,
        tab=tab,
        info=info,
        logged_in=bool(user),
        has_support_contact=any(
            info.get(k)
            for k in (
                "support_email",
                "support_phone",
                "support_telegram",
                "support_url",
                "documentation_url",
            )
        ),
    )


@router.get("/about", response_class=HTMLResponse)
async def about_page(request: Request, tab: str = Query("about")):
    safe_tab = tab if tab in ("about", "support", "license", "legal", "security", "updates", "system") else "about"
    return render_template(request, "about/index.html", await _about_context(request, safe_tab))


@router.get("/support", response_class=HTMLResponse)
async def support_page(request: Request):
    return RedirectResponse(url="/about?tab=support", status_code=303)
