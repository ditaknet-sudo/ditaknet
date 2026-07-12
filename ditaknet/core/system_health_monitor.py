"""Periodic system health checks that create admin notifications."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from loguru import logger

from ditaknet import database as db
from ditaknet.config import settings
from ditaknet.core.backup import list_backups
from ditaknet.core.notifications_service import notify, notify_system_problem, notify_update_available, notify_customer_notices
from ditaknet.core.updates import get_update_status
from ditaknet.health import deep_health
from ditaknet.utils.paths import directory_status


async def run_system_health_checks() -> dict:
    """Run health checks and emit deduplicated notifications."""
    results: dict = {"checked_at": datetime.now(UTC).isoformat(), "issues": []}

    try:
        health = await deep_health()
        if health.get("overall_status") == "fail":
            await notify_system_problem(
                "System health check failed",
                "One or more critical components are unhealthy. Open Server Health for details.",
                dedupe_key="health:critical",
                level="critical",
            )
            results["issues"].append("health_fail")
    except Exception as exc:
        logger.warning("Health check job failed: {}", exc)

    for name, path in (
        ("data", settings.data_dir_path),
        ("logs", settings.log_dir_path),
        ("backups", settings.backup_dir_path),
    ):
        info = directory_status(path)
        if not info.get("ok"):
            await notify_system_problem(
                f"{name.title()} directory not writable",
                f"Directory {path} is not writable. DitakNet may fail to persist data.",
                dedupe_key=f"dir:{name}",
                level="error",
            )
            results["issues"].append(f"dir_{name}")

    try:
        from ditaknet.api.deps import get_scheduler

        sched = await get_scheduler().status()
        if settings.scheduler_enabled and not sched.get("running"):
            await notify_system_problem(
                "Scheduler stopped",
                "The monitoring scheduler is not running. Checks may not execute.",
                dedupe_key="scheduler:stopped",
                level="critical",
            )
            results["issues"].append("scheduler_stopped")
    except Exception:
        pass

    backups = list_backups()
    if backups:
        latest = backups[0]
        try:
            created = datetime.fromisoformat(str(latest.get("created_at")).replace("Z", "+00:00"))
            if datetime.now(UTC) - created > timedelta(days=7):
                await notify_system_problem(
                    "Backup is older than 7 days",
                    f"Latest backup: {latest.get('filename')}. Consider creating a new backup.",
                    dedupe_key="backup:stale",
                    level="warning",
                )
                results["issues"].append("backup_stale")
        except Exception:
            pass
    else:
        await notify_system_problem(
            "No backups found",
            "Create a full backup to protect your monitoring data.",
            dedupe_key="backup:none",
            level="warning",
        )
        results["issues"].append("backup_none")

    try:
        from ditaknet.core.licensing import license_service

        lic = await license_service.status()
        expires = lic.get("expires_at")
        if expires:
            try:
                exp_dt = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
                if exp_dt - datetime.now(UTC) < timedelta(days=14):
                    await notify(
                        level="warning",
                        category="license",
                        title="License expiring soon",
                        message=f"License expires on {expires}",
                        action_url="/license",
                        dedupe_key="license:expiring",
                    )
                    results["issues"].append("license_expiring")
            except Exception:
                pass
    except Exception:
        pass

    try:
        update_status = await get_update_status()
        if update_status.get("update_available"):
            await notify_update_available(update_status)
            results["issues"].append("update_available")
        await notify_customer_notices(update_status)
        if update_status.get("customer_notice_available"):
            results["issues"].append("customer_notice")
        if update_status.get("status") == "error":
            await notify_system_problem(
                "Update check failed",
                update_status.get("message") or "Could not check for updates",
                dedupe_key="update:check_failed",
                level="warning",
            )
    except Exception as exc:
        logger.debug("Update check in health job: {}", exc)

    failed_scans = await db.count_system_logs_since(hours=24, category="discovery", level="error")
    if failed_scans >= 3:
        await notify_system_problem(
            "Discovery errors detected",
            f"{failed_scans} discovery errors in the last 24 hours.",
            dedupe_key="discovery:errors",
            level="warning",
        )
        results["issues"].append("discovery_errors")

    return results
