"""Health dashboard payload for system logs and activity UI."""

from __future__ import annotations

from typing import Any

from ditaknet import database as db
from ditaknet.api.v1.system import _scheduler_payload
from ditaknet.config import settings
from ditaknet.core.features import feature_flags_from_license
from ditaknet.core.licensing import license_service
from ditaknet.core.system_log_service import uptime_seconds
from ditaknet.core.system_metrics import collect_system_metrics
from ditaknet.discovery.scheduler import discovery_scheduler
from ditaknet.health import deep_health
from ditaknet.utils.paths import directory_status


def parse_subnets(scan: dict[str, Any]) -> list[str]:
    raw = scan.get("subnets_json") or scan.get("subnets") or "[]"
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    try:
        import json

        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
    except Exception:
        pass
    return []


def merge_progress(scan: dict[str, Any], live: dict[str, Any] | None) -> dict[str, Any]:
    live = live or {}
    return {
        "percent": int(live.get("percent") or scan.get("progress_percent") or 0),
        "scanned": int(live.get("scanned") or scan.get("scanned_hosts") or 0),
        "total": int(live.get("total") or scan.get("total_hosts") or 0),
        "found": int(live.get("found") or scan.get("found_count") or 0),
        "current_ip": live.get("current_ip") or scan.get("current_ip") or "",
        "stage": live.get("stage") or scan.get("current_stage") or "",
        "stage_message": live.get("stage_message") or scan.get("stage_message") or "",
    }

FRIENDLY_MODULE_NAMES: dict[str, str] = {
    "discovery_enabled": "Network Discovery",
    "topology_enabled": "Topology",
    "agent_enabled": "Agents",
    "advanced_reports_enabled": "Advanced Reports",
    "employee_attendance_enabled": "Employee Attendance",
    "departments_enabled": "Departments",
    "employee_groups_enabled": "Employee Groups",
    "shifts_enabled": "Shifts",
    "monthly_work_hours_enabled": "Monthly Work Hours",
    "multi_office_enabled": "Multi-Office",
    "branch_agent_enabled": "Branch Agents",
}

NOISE_MESSAGE_SNIPPETS = (
    "heartbeat check completed",
    "offline agents: 0",
    "checking agent heartbeats",
    "scheduler tick",
    "discovery refresh completed; started=false",
    "discovery refresh completed; started=False",
)

GATED_JOB_TYPES = frozenset(
    {
        "branch_heartbeat",
        "branch_agent",
        "attendance_refresh",
    }
)

IMPORTANT_EVENT_TYPES = frozenset(
    {
        "check_failed",
        "job_failed",
        "scan_failed",
        "alert_created",
        "notification_failed",
        "server_startup",
        "server_shutdown",
        "scheduler_stopped",
        "recovery_detected",
        "service_state_changed",
    }
)


def friendly_module_names(flags: dict[str, bool] | None) -> list[str]:
    flags = flags or {}
    return sorted(
        FRIENDLY_MODULE_NAMES[key]
        for key, enabled in flags.items()
        if enabled and key in FRIENDLY_MODULE_NAMES
    )


def is_noisy_event(row: dict[str, Any]) -> bool:
    message = str(row.get("message") or "").lower()
    event_type = str(row.get("event_type") or "").lower()
    if any(snippet in message for snippet in NOISE_MESSAGE_SNIPPETS):
        return True
    if event_type in {"heartbeat_ok", "scheduler_tick", "check_ok"}:
        return True
    if "heartbeat check completed" in message and row.get("level") == "info":
        return True
    return False


def is_important_event(
    event_type: str | None,
    *,
    level: str | None = None,
    message: str | None = None,
) -> bool:
    if is_noisy_event({"event_type": event_type, "level": level, "message": message}):
        return False
    normalized = str(event_type or "").strip().lower()
    if normalized in IMPORTANT_EVENT_TYPES:
        return True
    if normalized in {"scan_started", "scan_completed", "scan_failed", "device_found", "license_changed"}:
        return True
    level_norm = str(level or "").strip().lower()
    return level_norm in {"error", "critical", "warning"}


def filter_jobs_for_license(jobs: list[dict[str, Any]], flags: dict[str, bool] | None) -> list[dict[str, Any]]:
    flags = flags or {}
    filtered: list[dict[str, Any]] = []
    for job in jobs:
        job_type = str(job.get("type") or "")
        if job_type in {"branch_heartbeat", "branch_agent"} and not flags.get("branch_agent_enabled"):
            continue
        if job_type == "attendance_refresh" and not flags.get("employee_attendance_enabled"):
            continue
        filtered.append(job)
    return filtered


def preview_events(logs: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for row in logs:
        if is_important_event(
            row.get("event_type"),
            level=row.get("level"),
            message=row.get("message"),
        ):
            selected.append(row)
        if len(selected) >= limit:
            break
    return selected


async def _health_summary_base() -> dict[str, Any]:
    deep = await deep_health()
    deployment = settings.app_deployment_mode.strip() or "unknown"
    if deployment.lower() == "unknown" and settings.is_production:
        deployment = "docker"
    license_status = await license_service.status()
    flags = feature_flags_from_license(license_status)
    last_check_at = await db.get_last_check_timestamp()
    last_error_at = await db.get_last_system_log_timestamp(levels=["error", "critical"])
    notifications = deep.get("notifications", {})
    return {
        "app_status": deep.get("status", "unknown"),
        "scheduler_status": "running" if deep.get("scheduler", {}).get("running") else "stopped",
        "database_status": "connected" if deep.get("database", {}).get("ok") else "error",
        "log_path_status": "ok" if deep.get("directories", {}).get("logs", {}).get("ok") else "error",
        "deployment_mode": deployment,
        "app_version": settings.app_version,
        "uptime_seconds": uptime_seconds(),
        "current_port": settings.app_port,
        "data_dir_writable": directory_status(settings.data_dir_path).get("writable", False),
        "log_dir_writable": directory_status(settings.log_dir_path).get("writable", False),
        "backup_dir_writable": directory_status(settings.backup_dir_path).get("writable", False),
        "notifications_enabled": settings.telegram_enabled,
        "telegram_configured": bool(notifications.get("telegram_configured")),
        "license_package": license_status.get("tier", "FREE"),
        "license_status": license_status.get("status", "active"),
        "data_dir": str(settings.data_dir_path),
        "log_dir": str(settings.log_dir_path),
        "backup_dir": str(settings.backup_dir_path),
        "active_modules": friendly_module_names(flags),
        "feature_flags": flags,
        "last_check_at": last_check_at,
        "last_error_at": last_error_at,
    }


async def _discovery_live() -> list[dict[str, Any]]:
    scans = await db.list_discovery_scans(limit=5)
    live_scans: list[dict[str, Any]] = []
    for scan in scans:
        if scan.get("status") not in {"pending", "running"}:
            continue
        scan_id = int(scan["id"])
        progress = merge_progress(scan, discovery_scheduler.get_progress(scan_id))
        subnets = parse_subnets(scan)
        events = await db.get_discovery_scan_events(scan_id, limit=20)
        live_scans.append(
            {
                "scan_id": scan_id,
                "status": scan.get("status"),
                "profile": scan.get("profile"),
                "subnet": subnets[0] if subnets else "",
                "subnets": subnets,
                "current_ip": progress.get("current_ip") or scan.get("current_ip") or "",
                "current_stage": progress.get("stage") or scan.get("current_stage") or "",
                "stage": progress.get("stage") or scan.get("current_stage") or "",
                "scanned": progress.get("scanned", 0),
                "total": progress.get("total", 0),
                "found": progress.get("found", 0),
                "percent": progress.get("percent", 0),
                "progress_percent": progress.get("percent", 0),
                "stage_message": progress.get("stage_message") or scan.get("stage_message") or "",
                "recent_events": [
                    {
                        "id": event.get("id"),
                        "timestamp": event.get("timestamp"),
                        "level": event.get("level"),
                        "event_type": event.get("event_type"),
                        "message": event.get("message"),
                        "ip_address": event.get("ip_address"),
                    }
                    for event in reversed(events)
                ],
            }
        )
    return live_scans


def _overall_status(
    *,
    health: dict[str, Any],
    summary: dict[str, Any],
    metrics: dict[str, Any] | None,
) -> str:
    if health.get("database_status") != "connected":
        return "critical"
    if health.get("app_status") == "degraded":
        return "warning"
    if summary.get("errors_last_24h", 0) > 0 and summary.get("active_jobs_count", 0) == 0:
        if summary.get("checks_running", 0) == 0 and summary.get("discovery_running", 0) == 0:
            return "warning"
    if metrics:
        cpu = metrics.get("cpu_percent")
        memory = metrics.get("ram_percent")
        disk = metrics.get("disk_percent")
        if isinstance(cpu, (int, float)) and cpu >= 90:
            return "critical" if cpu >= 95 else "warning"
        if isinstance(memory, (int, float)) and memory >= 90:
            return "critical" if memory >= 95 else "warning"
        if isinstance(disk, (int, float)) and disk >= 90:
            return "critical"
    if summary.get("active_jobs_count", 0) > 0 or summary.get("discovery_running", 0) > 0:
        return "busy"
    if summary.get("errors_last_24h", 0) > 5:
        return "warning"
    return "healthy"


async def build_health_dashboard() -> dict[str, Any]:
    from ditaknet.core.activity_service import activity_service

    health = await _health_summary_base()
    summary = await activity_service.get_summary()
    metrics = collect_system_metrics()
    discovery = await _discovery_live()
    scheduler = await _scheduler_payload()
    recent_logs = await db.list_system_logs(limit=100, offset=0)
    errors_last_24h = await db.count_system_logs_since(hours=24, levels=["error", "critical"])
    warnings_last_24h = await db.count_system_logs_since(hours=24, level="warning")
    status = _overall_status(health=health, summary=summary, metrics=metrics)
    flags = health.get("feature_flags") or {}
    active_jobs = filter_jobs_for_license(activity_service.get_active_jobs(), flags)
    running_checks = [
        job for job in active_jobs if str(job.get("type")) == "monitoring_check"
    ][:5]
    last_error = await db.get_last_system_log(levels=["error", "critical"])
    last_warning = await db.get_last_system_log(levels=["warning"])
    important = preview_events(recent_logs, limit=5)
    compact = {
        "overall_status": status,
        "cpu_percent": metrics.get("cpu_percent"),
        "ram_percent": metrics.get("ram_percent"),
        "disk_percent": metrics.get("disk_percent"),
        "uptime_seconds": health.get("uptime_seconds"),
        "scheduler_status": health.get("scheduler_status"),
        "database_status": health.get("database_status"),
        "active_jobs_count": len(active_jobs),
        "checks_running_count": summary.get("checks_running", 0),
        "discovery_running": bool(discovery),
        "last_error": last_error,
        "last_warning": last_warning,
        "deployment_mode": health.get("deployment_mode"),
        "current_port": health.get("current_port"),
        "app_version": health.get("app_version"),
        "license_package": health.get("license_package"),
        "active_modules_friendly": health.get("active_modules"),
        "logs_available": True,
        "metrics_available": bool(metrics.get("available")),
    }
    return {
        "overall_status": status,
        "status": status,
        "compact": compact,
        "health": health,
        "summary": summary,
        "metrics": metrics,
        "scheduler": scheduler,
        "discovery": discovery,
        "active_jobs": active_jobs,
        "running_checks": running_checks,
        "errors_last_24h": errors_last_24h,
        "warnings_last_24h": warnings_last_24h,
        "checks_run_today": await db.count_check_results_since(hours=24),
        "failed_checks_today": await db.count_failed_checks_since(hours=24),
        "important_events": important,
        "preview_events": important,
        "workload": {
            "active_jobs": len(active_jobs),
            "checks_running": summary.get("checks_running", 0),
            "discovery_running": bool(discovery),
            "scheduler_status": health.get("scheduler_status"),
        },
    }
