"""Updates settings page (/settings/updates)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from ditaknet.config import settings
from ditaknet.core.build_metadata import build_metadata
from ditaknet.core.updates import get_update_status, is_update_check_enabled
from ditaknet.i18n import translate
from ditaknet.security import require_web_permissions, verify_web_csrf
from ditaknet.web.routes import render_template

router = APIRouter(include_in_schema=False, dependencies=[Depends(verify_web_csrf)])

UPDATES_ASSET_BUILD = "20260722upd4"


@router.get("/settings/updates", response_class=HTMLResponse)
async def settings_updates_page(
    request: Request,
    _user: str = Depends(require_web_permissions("settings.updates")),
):
    meta = build_metadata(friendly_missing=True)
    update_status = await get_update_status()
    lang = request.session.get("lang", "en")
    ghcr = settings.ghcr_image.strip() or update_status.get("ghcr_image") or ""
    github_repo = (
        settings.github_repository.strip() or meta.get("github_repository") or "—"
    )
    backup_configured = settings.backup_dir_path.exists()
    return render_template(
        request,
        "settings/updates.html",
        {
            "lang": lang,
            "active": "updates",
            "settings": settings,
            "build_meta": meta,
            "update_status": update_status,
            "update_checks_enabled": await is_update_check_enabled(),
            "ghcr_image": ghcr,
            "github_repository": github_repo,
            "backup_configured": backup_configured,
            "asset_build": UPDATES_ASSET_BUILD,
            "t": lambda k, **kw: translate(k, lang, **kw),
        },
    )
