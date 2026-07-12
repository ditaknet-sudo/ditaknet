"""First-run setup wizard — guided flow without blocking license request forms."""

from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ditaknet import database as db
from ditaknet.core.licensing import LicenseLimitError, license_service
from ditaknet.core.setup_state import (
    SETUP_STEPS,
    complete_setup,
    get_network_plan,
    get_setup_scan_id,
    get_setup_step,
    get_setup_subnet,
    save_admin_credentials,
    save_default_language,
    save_imported_count,
    save_monitoring_use_case,
    save_network_plan,
    save_setup_scan_id,
    save_setup_subnet,
    save_system_name,
    set_setup_step,
)
from ditaknet.core.packages import use_cases_payload
from ditaknet.config import settings
from ditaknet.discovery.scheduler import discovery_scheduler
from ditaknet.discovery.subnet import (
    detect_local_subnets,
    is_cgnat_subnet,
    normalize_subnets,
    pick_primary_subnet,
    suggest_subnet_for_type,
)
from ditaknet.i18n import language_label, supported_languages, translate
from ditaknet.security import AuthenticatedUser, hash_password
from ditaknet.web.routes import render_template

router = APIRouter(include_in_schema=False)

STEPS = list(SETUP_STEPS)
LANGUAGE_LABELS = {code: language_label(code) for code in supported_languages()}


def _license_error_message(exc: Exception, lang: str) -> str:
    if isinstance(exc, LicenseLimitError) and exc.error_key:
        return translate(exc.error_key, lang, **exc.params)
    return str(exc)


def _ctx(request: Request, **extra):
    lang = request.session.get("lang", "en")
    step = extra.get("step") or "language"
    try:
        idx = STEPS.index(step)
    except ValueError:
        idx = 0
    base = {
        "lang": lang,
        "t": lambda k, **kw: translate(k, lang, **kw),
        "languages": supported_languages(),
        "language_labels": LANGUAGE_LABELS,
        "step": step,
        "step_num": idx + 1,
        "step_total": len(STEPS),
        "documentation_url": settings.app_documentation_url.strip(),
        "support_url": settings.app_support_url.strip(),
    }
    base.update(extra)
    return base


async def _guard_setup(request: Request):
    if await db.is_setup_complete():
        return RedirectResponse(url="/dashboard", status_code=303)
    return None


@router.get("/setup", response_class=HTMLResponse)
async def setup_start(request: Request):
    if redirect := await _guard_setup(request):
        return redirect
    step = await get_setup_step()
    if step == "language":
        return render_template(request, "setup/index.html", _ctx(request, step="language"))
    return RedirectResponse(url=f"/setup/{step}", status_code=303)


@router.get("/setup/language", response_class=HTMLResponse)
async def setup_language_get(request: Request):
    if redirect := await _guard_setup(request):
        return redirect
    return render_template(request, "setup/index.html", _ctx(request, step="language"))


@router.post("/setup/language")
async def setup_language_post(request: Request, language: str = Form("en")):
    if language in supported_languages():
        request.session["lang"] = language
        await save_default_language(language)
    await set_setup_step("purpose")
    return RedirectResponse(url="/setup/purpose", status_code=303)


@router.get("/setup/purpose", response_class=HTMLResponse)
async def setup_purpose_get(request: Request):
    if redirect := await _guard_setup(request):
        return redirect
    return render_template(
        request,
        "setup/purpose.html",
        _ctx(request, step="purpose", use_cases=use_cases_payload()),
    )


@router.post("/setup/purpose")
async def setup_purpose_post(request: Request, use_case: str = Form("home_small_office")):
    if use_case in {u["id"] for u in use_cases_payload()}:
        await save_monitoring_use_case(use_case)
    await set_setup_step("admin")
    return RedirectResponse(url="/setup/admin", status_code=303)


@router.get("/setup/package", response_class=HTMLResponse)
@router.get("/setup/packages", response_class=HTMLResponse)
@router.get("/setup/package/detail", response_class=HTMLResponse)
@router.get("/setup/activate", response_class=HTMLResponse)
@router.get("/setup/license", response_class=HTMLResponse)
@router.get("/setup/license/activate", response_class=HTMLResponse)
@router.post("/setup/package")
@router.post("/setup/packages")
@router.post("/setup/activate")
@router.post("/setup/license")
@router.post("/setup/license/activate")
async def retired_license_setup_step(request: Request):
    if await db.is_setup_complete():
        return RedirectResponse(url="/license", status_code=303)
    await set_setup_step("admin")
    return RedirectResponse(url="/setup/admin", status_code=303)


@router.get("/setup/admin", response_class=HTMLResponse)
async def setup_admin_get(request: Request):
    if redirect := await _guard_setup(request):
        return redirect
    return render_template(request, "setup/admin.html", _ctx(request, step="admin"))


@router.post("/setup/admin")
async def setup_admin_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    system_name: str = Form("DitakNet"),
):
    lang = request.session.get("lang", "en")
    if password != password_confirm:
        return render_template(
            request,
            "setup/admin.html",
            _ctx(request, step="admin", error=translate("setup.error.password_mismatch", lang)),
            status_code=400,
        )
    if len(password) < 8:
        return render_template(
            request,
            "setup/admin.html",
            _ctx(request, step="admin", error=translate("setup.error.password_short", lang)),
            status_code=400,
        )
    await save_admin_credentials(username.strip(), hash_password(password))
    await save_system_name(system_name.strip() or "DitakNet")
    request.session["user"] = username.strip()
    request.session["role"] = "admin"
    await set_setup_step("network")
    return RedirectResponse(url="/setup/network", status_code=303)


@router.get("/setup/network", response_class=HTMLResponse)
async def setup_network_get(request: Request):
    if redirect := await _guard_setup(request):
        return redirect
    plan = await get_network_plan()
    return render_template(
        request,
        "setup/network.html",
        _ctx(request, step="network", plan=plan, license=await license_service.status()),
    )


@router.post("/setup/network")
async def setup_network_post(
    request: Request,
    network_count: str = Form("1"),
    device_count: str = Form("100"),
    network_type: str = Form("192.168"),
):
    await save_network_plan(
        network_count=network_count,
        device_count=device_count,
        network_type=network_type,
    )
    await set_setup_step("subnet")
    return RedirectResponse(url="/setup/subnet", status_code=303)


@router.get("/setup/subnet", response_class=HTMLResponse)
async def setup_subnet_get(request: Request):
    if redirect := await _guard_setup(request):
        return redirect
    local_subnets = detect_local_subnets()
    plan = await get_network_plan()
    suggested = suggest_subnet_for_type(plan.get("network_type", "192.168"), local_subnets)
    saved = await get_setup_subnet()
    return render_template(
        request,
        "setup/subnet.html",
        _ctx(
            request,
            step="subnet",
            local_subnets=local_subnets,
            selected_subnet=saved or suggested,
            license=await license_service.status(),
        ),
    )


@router.post("/setup/subnet")
async def setup_subnet_post(
    request: Request,
    action: str = Form("scan"),
    subnets: str = Form(""),
):
    lang = request.session.get("lang", "en")
    local_subnets = detect_local_subnets()
    subnet_list: list[str] = []

    if action == "skip":
        await set_setup_step("finish")
        return RedirectResponse(url="/setup/finish", status_code=303)

    if action in {"detect", "scan_detected"} and local_subnets:
        subnet_list = [pick_primary_subnet(local_subnets)]
    elif subnets.strip():
        subnet_list = [s.strip() for s in subnets.split(",") if s.strip()]
    else:
        return render_template(
            request,
            "setup/subnet.html",
            _ctx(
                request,
                step="subnet",
                local_subnets=local_subnets,
                selected_subnet=subnets,
                license=await license_service.status(),
                error=translate("setup.subnet.required", lang),
            ),
            status_code=400,
        )

    try:
        normalized = normalize_subnets(subnet_list)
        if not normalized:
            raise ValueError(translate("setup.subnet.required", lang))
        if is_cgnat_subnet(normalized[0]):
            raise ValueError(translate("setup.subnet.cgnat_warning", lang))
        await license_service.enforce_discovery_scan(normalized)
        await save_setup_subnet(normalized[0])
        scan = await db.create_discovery_scan("quick", json.dumps(normalized))
        await save_setup_scan_id(scan["id"])
        request.session["setup_scan_id"] = scan["id"]
        await discovery_scheduler.start_scan(scan["id"], normalized, "quick")
        await set_setup_step("discovery")
        return RedirectResponse(url="/setup/discovery", status_code=303)
    except (ValueError, LicenseLimitError) as exc:
        return render_template(
            request,
            "setup/subnet.html",
            _ctx(
                request,
                step="subnet",
                local_subnets=local_subnets,
                selected_subnet=subnet_list[0] if subnet_list else subnets,
                license=await license_service.status(),
                error=_license_error_message(exc, lang),
            ),
            status_code=400,
        )


@router.get("/setup/discovery", response_class=HTMLResponse)
async def setup_discovery_get(request: Request):
    if redirect := await _guard_setup(request):
        return redirect
    scan_id = await get_setup_scan_id() or request.session.get("setup_scan_id")
    scan = None
    progress = {"percent": 0, "scanned": 0, "total": 0, "found": 0}
    if scan_id:
        scan = await db.get_discovery_scan(int(scan_id))
        progress = discovery_scheduler.get_progress(int(scan_id))
        if scan:
            progress = {
                "percent": int(scan.get("progress_percent") or progress.get("percent") or 0),
                "scanned": int(scan.get("scanned_hosts") or progress.get("scanned") or 0),
                "total": int(scan.get("total_hosts") or progress.get("total") or 0),
                "found": int(scan.get("found_count") or progress.get("found") or 0),
                "status": scan.get("status") or "pending",
            }
    if not scan_id:
        return RedirectResponse(url="/setup/subnet", status_code=303)
    return render_template(
        request,
        "setup/discovery.html",
        _ctx(request, step="discovery", scan=scan, scan_id=scan_id, progress=progress),
    )


@router.get("/setup/discovery/progress")
async def setup_discovery_progress(request: Request):
    scan_id = await get_setup_scan_id() or request.session.get("setup_scan_id")
    if not scan_id:
        return JSONResponse({"error": "no_scan"}, status_code=404)
    scan = await db.get_discovery_scan(int(scan_id))
    live = discovery_scheduler.get_progress(int(scan_id))
    if not scan:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return {
        "status": scan.get("status"),
        "percent": int(scan.get("progress_percent") or live.get("percent") or 0),
        "scanned": int(scan.get("scanned_hosts") or live.get("scanned") or 0),
        "total": int(scan.get("total_hosts") or live.get("total") or 0),
        "found": int(scan.get("found_count") or live.get("found") or 0),
        "current_ip": live.get("current_ip") or "",
    }


@router.post("/setup/discovery")
async def setup_discovery_post(request: Request, action: str = Form("continue")):
    if action == "skip":
        await set_setup_step("finish")
        return RedirectResponse(url="/setup/finish", status_code=303)
    scan_id = await get_setup_scan_id() or request.session.get("setup_scan_id")
    devices = []
    if scan_id:
        devices = await db.list_discovered_devices(scan_id=int(scan_id))
    if devices:
        await set_setup_step("import")
        return RedirectResponse(url="/setup/import", status_code=303)
    await set_setup_step("finish")
    return RedirectResponse(url="/setup/finish", status_code=303)


@router.get("/setup/import", response_class=HTMLResponse)
async def setup_import_get(request: Request):
    if redirect := await _guard_setup(request):
        return redirect
    scan_id = await get_setup_scan_id() or request.session.get("setup_scan_id")
    devices = await db.list_discovered_devices(scan_id=int(scan_id)) if scan_id else []
    if not devices:
        return RedirectResponse(url="/setup/finish", status_code=303)
    return render_template(
        request,
        "setup/import.html",
        _ctx(request, step="import", devices=devices, scan_id=scan_id),
    )


@router.post("/setup/import")
async def setup_import_post(
    request: Request,
    device_ids: list[int] = Form(default=[]),
    action: str = Form("import"),
):
    if action == "skip":
        await set_setup_step("finish")
        return RedirectResponse(url="/setup/finish", status_code=303)

    from ditaknet.api.discovery import ImportRequest, import_devices

    user = AuthenticatedUser(username=request.session.get("user", "admin"), role="admin")
    if device_ids:
        await import_devices(ImportRequest(device_ids=device_ids, create_checks=True), user)
        await save_imported_count(len(device_ids))
    await set_setup_step("finish")
    return RedirectResponse(url="/setup/finish", status_code=303)


@router.get("/setup/finish", response_class=HTMLResponse)
async def setup_finish_get(request: Request):
    if redirect := await _guard_setup(request):
        return redirect
    scan_id = await get_setup_scan_id() or request.session.get("setup_scan_id")
    devices = []
    if scan_id:
        devices = await db.list_discovered_devices(scan_id=int(scan_id))
    system_name = await db.get_app_setting("system_name") or "DitakNet"
    imported = await db.list_hosts()
    return render_template(
        request,
        "setup/finish.html",
        _ctx(
            request,
            step="finish",
            devices=devices,
            imported_count=len(imported),
            system_name=system_name,
            license=await license_service.status(),
            subnet=await get_setup_subnet(),
        ),
    )


@router.post("/setup/finish")
async def setup_finish_post(request: Request):
    await complete_setup()
    return RedirectResponse(url="/dashboard", status_code=303)


# Legacy routes — redirect to the new flow
@router.get("/setup/system", response_class=HTMLResponse)
async def setup_system_legacy(request: Request):
    return RedirectResponse(url="/setup/network", status_code=303)


@router.post("/setup/system")
async def setup_system_legacy_post(request: Request):
    return RedirectResponse(url="/setup/network", status_code=303)


@router.get("/setup/notifications", response_class=HTMLResponse)
async def setup_notifications_legacy(request: Request):
    return RedirectResponse(url="/setup/subnet", status_code=303)


@router.post("/setup/notifications")
async def setup_notifications_legacy_post(request: Request):
    return RedirectResponse(url="/setup/subnet", status_code=303)


@router.get("/setup/restore", response_class=HTMLResponse)
async def setup_restore_get(request: Request):
    if redirect := await _guard_setup(request):
        return redirect
    return render_template(request, "setup/restore.html", _ctx(request, step="restore"))


@router.post("/setup/restore")
async def setup_restore_post(
    request: Request,
    backup: UploadFile = File(...),
    admin_username: str = Form(...),
    admin_password: str = Form(...),
    confirm: str = Form(""),
):
    if redirect := await _guard_setup(request):
        return redirect
    lang = request.session.get("lang", "en")
    if confirm != "yes":
        return render_template(
            request,
            "setup/restore.html",
            _ctx(request, step="restore", error=translate("backups.confirm_restore", lang)),
            status_code=400,
        )
    import tempfile
    from pathlib import Path

    from ditaknet.core.notifications_service import notify_restore_result
    from ditaknet.core.restore import restore_from_uploaded_file

    suffix = Path(backup.filename or "").suffix.lower()
    if suffix not in {".zip", ".sqlite3", ".db", ".sqlite"}:
        return render_template(
            request,
            "setup/restore.html",
            _ctx(request, step="restore", error=translate("backups.invalid_file", lang)),
            status_code=400,
        )
    try:
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / Path(backup.filename or "upload.zip").name
            content = await backup.read()
            dest.write_bytes(content)
            await restore_from_uploaded_file(
                dest,
                mode="full_restore_reset_admin",
                confirm=True,
                new_admin_username=admin_username.strip(),
                new_admin_password=admin_password,
                actor="setup",
            )
        await notify_restore_result(success=True, filename=backup.filename or "upload")
        request.session["user"] = admin_username.strip()
        return RedirectResponse(url="/dashboard", status_code=303)
    except Exception as exc:
        await notify_restore_result(success=False, filename=backup.filename or "", detail=str(exc))
        return render_template(
            request,
            "setup/restore.html",
            _ctx(request, step="restore", error=str(exc)),
            status_code=500,
        )


@router.get("/support/license-request", response_class=HTMLResponse)
async def support_license_request(request: Request):
    if await db.is_setup_complete():
        return RedirectResponse(url="/license", status_code=303)
    return RedirectResponse(url="/setup/admin", status_code=303)
