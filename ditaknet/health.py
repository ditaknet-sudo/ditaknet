"""
Health endpoints for Docker, TrueNAS, and load balancers.

``/health`` is a cheap liveness probe; ``/health/deep`` verifies DB, scheduler,
schema, license, settings, and mounted DATA/BACKUP/LOG directories.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from ditaknet import database as db
from ditaknet.api.deps import get_scheduler
from ditaknet.config import settings
from ditaknet.core.build_metadata import build_metadata
from ditaknet.utils.paths import directory_status


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_status(ok: bool, *, partial: bool = False) -> str:
    if ok:
        return "pass"
    if partial:
        return "partial"
    return "fail"


async def basic_health() -> dict:
    meta = build_metadata(friendly_missing=True)
    return {
        "status": "healthy",
        "app_name": settings.app_name,
        "version": meta["version"],
        "build_commit": meta["build_commit"],
        "build_date": meta["build_date"],
        "image_tag": meta["image_tag"],
        "github_repository": meta["github_repository"],
        "ghcr_image": meta["ghcr_image"],
        "timestamp": _utc_now(),
    }


async def _license_health() -> dict[str, Any]:
    try:
        from ditaknet.core.licensing import license_service

        status = await license_service.status()
        return {
            "status": "pass",
            "ok": True,
            "tier": status.get("tier"),
            "valid": bool(status.get("valid", True)),
        }
    except Exception as exc:
        return {"status": "fail", "ok": False, "error": type(exc).__name__}


async def _settings_health() -> dict[str, Any]:
    try:
        setup = await db.get_app_setting("setup_complete")
        return {
            "status": "pass",
            "ok": True,
            "setup_complete": setup == "1",
        }
    except Exception as exc:
        return {"status": "fail", "ok": False, "error": type(exc).__name__}


def _static_assets_health() -> dict[str, Any]:
    root = os.path.join(os.path.dirname(__file__), "static")
    required = (
        "css/app.css",
        "vendor/bootstrap/bootstrap.min.css",
        "vendor/bootstrap/bootstrap.bundle.min.js",
        "js/app.js",
    )
    missing = [rel for rel in required if not os.path.isfile(os.path.join(root, rel))]
    ok = not missing
    return {
        "status": _check_status(ok),
        "ok": ok,
        "missing_assets": missing,
    }


async def deep_health() -> dict:
    dirs = {
        "data": directory_status(settings.data_dir_path),
        "backups": directory_status(settings.backup_dir_path),
        "logs": directory_status(settings.log_dir_path),
    }

    db_status = {"ok": False, "type": "sqlite", "path": str(settings.db_path), "status": "fail"}
    try:
        connection = await db.get_db()
        await connection.execute_fetchall("SELECT 1")
        db_status["ok"] = True
        db_status["status"] = "pass"
    except Exception as exc:
        db_status["error"] = type(exc).__name__

    schema = await db.schema_health()
    license_state = await _license_health()
    app_settings = await _settings_health()
    static_assets = _static_assets_health()

    scheduler_status: dict[str, Any] = {"running": False, "job_count": 0, "status": "partial"}
    try:
        scheduler = get_scheduler()
        if hasattr(scheduler, "status"):
            scheduler_status = await scheduler.status()
        else:
            sched_obj = getattr(scheduler, "_scheduler", None)
            jobs = sched_obj.get_jobs() if sched_obj and hasattr(sched_obj, "get_jobs") else []
            scheduler_status = {
                "running": bool(getattr(sched_obj, "running", False)),
                "job_count": len(jobs),
            }
        scheduler_status["status"] = _check_status(bool(scheduler_status.get("running")))
    except RuntimeError:
        scheduler_status = {
            "running": False,
            "job_count": 0,
            "status": "partial",
            "error": "not_initialised",
        }
    except Exception as exc:
        scheduler_status = {"running": False, "status": "fail", "error": type(exc).__name__}

    notifications = {
        "telegram_configured": settings.telegram_enabled,
        "console_fallback": True,
        "status": "pass",
    }

    checks = {
        "database": db_status,
        "schema": schema,
        "directories": {
            "status": _check_status(all(info.get("ok") for info in dirs.values())),
            "items": dirs,
        },
        "scheduler": scheduler_status,
        "license": license_state,
        "settings": app_settings,
        "static_assets": static_assets,
        "notifications": notifications,
    }

    critical_ok = db_status["ok"] and schema.get("ok") and all(info.get("ok") for info in dirs.values())
    scheduler_ok = not settings.scheduler_enabled or bool(scheduler_status.get("running"))
    all_ok = critical_ok and scheduler_ok and license_state.get("ok") and app_settings.get("ok")

    failed = [
        name
        for name, block in checks.items()
        if isinstance(block, dict) and block.get("status") == "fail"
    ]
    partial = [
        name
        for name, block in checks.items()
        if isinstance(block, dict) and block.get("status") == "partial"
    ]

    overall = "healthy" if all_ok else ("degraded" if critical_ok else "unhealthy")

    return {
        "status": overall,
        "overall_status": "pass" if all_ok else ("partial" if critical_ok else "fail"),
        "app_name": settings.app_name,
        "version": settings.app_version,
        "build": build_metadata(friendly_missing=True),
        "timestamp": _utc_now(),
        "checks": checks,
        "failed_checks": failed,
        "partial_checks": partial,
        "database": db_status,
        "scheduler": scheduler_status,
        "directories": dirs,
        "notifications": notifications,
        "migrations": schema,
    }
