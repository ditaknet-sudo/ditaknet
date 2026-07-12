"""Domain and external access settings (/settings/domain)."""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ditaknet.config import settings
from ditaknet.security import require_web_permissions, verify_web_csrf
from ditaknet.web.routes import render_template

router = APIRouter(include_in_schema=False, dependencies=[Depends(verify_web_csrf)])


def _domain_status(base_url: str) -> dict[str, object]:
    parsed = urlparse(base_url.strip() or settings.app_base_url)
    scheme = (parsed.scheme or "http").lower()
    host = (parsed.hostname or "").lower()
    is_local = not host or host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local")
    https = scheme == "https"
    public_http = scheme == "http" and not is_local
    misconfigured = settings.is_production and (is_local or not https)
    return {
        "base_url": base_url.strip() or settings.app_base_url,
        "scheme": scheme or "http",
        "host": host or "—",
        "is_local": is_local,
        "https_configured": https,
        "public_http_warning": public_http,
        "misconfigured": misconfigured,
        "trusted_proxies": settings.trusted_proxies or "—",
        "cors_origins": settings.cors_origins,
        "session_cookie_secure": settings.session_cookie_secure,
    }


@router.get("/settings/domain", response_class=HTMLResponse)
async def settings_domain_page(
    request: Request,
    _user: str = Depends(require_web_permissions("settings.domain")),
):
    domain = _domain_status(settings.app_base_url)
    domain_warnings = ["domain_misconfigured"] if domain["misconfigured"] else []
    return render_template(
        request,
        "settings/domain.html",
        {
            "active": "domain",
            "domain": domain,
            "domain_warnings": domain_warnings,
            "settings": settings,
        },
    )


@router.get("/settings/external-access")
async def settings_external_access_redirect():
    return RedirectResponse(url="/settings/domain", status_code=303)
