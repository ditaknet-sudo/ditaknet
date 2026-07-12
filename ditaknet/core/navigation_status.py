"""Sidebar navigation status summary — safe badges for settings, license, health, discovery."""

from __future__ import annotations

from typing import Any

from ditaknet import database as db
from ditaknet.api.v1.system import _scheduler_payload
from ditaknet.config import settings
from ditaknet.core.activity_service import activity_service
from ditaknet.core.backup import list_backups
from ditaknet.core.licensing import license_service
from ditaknet.core.system_metrics import collect_system_metrics
from ditaknet.core.updates import get_update_status
from ditaknet.discovery.store import DEMO_DISCOVERY_SOURCES, list_discovery_inventory, list_monitored_networks
from ditaknet.health import deep_health
from ditaknet.security import AuthenticatedUser, has_permissions
from ditaknet.utils.paths import directory_status

_REASON_LEVELS: dict[str, str] = {
    "license_expired": "critical",
    "subnet_limit_exceeded": "critical",
    "license_limit_exceeded": "critical",
    "database_issue": "critical",
    "scheduler_stopped": "critical",
    "update_available": "warning",
    "backup_overdue": "warning",
    "domain_misconfigured": "warning",
    "security_warning": "warning",
    "notification_failed": "warning",
    "no_monitored_network": "warning",
    "scan_failed": "warning",
    "demo_data_detected": "warning",
    "pending_imports": "warning",
    "resource_pressure": "warning",
    "storage_not_writable": "warning",
    "unread_notifications": "info",
}

_IMPORTABLE_STATES = frozenset({"new", "active", "seen"})


def _block(reasons: list[str], *, count: int | None = None) -> dict[str, Any]:
    if not reasons:
        return {"level": "healthy", "count": 0, "reasons": []}
    levels = [_REASON_LEVELS.get(reason, "warning") for reason in reasons]
    if "critical" in levels:
        level = "critical"
    elif "warning" in levels:
        level = "warning"
    else:
        level = "info"
    return {
        "level": level,
        "count": count if count is not None else len(reasons),
        "reasons": reasons,
    }


async def _count_demo_discovery_rows() -> int:
    db_conn = await db.get_db()
    placeholders = ", ".join("?" for _ in DEMO_DISCOVERY_SOURCES)
    params = [s.lower() for s in DEMO_DISCOVERY_SOURCES]
    total = 0
    for table in ("discovered_devices", "discovery_inventory"):
        try:
            rows = await db_conn.execute_fetchall(
                f"SELECT COUNT(*) AS cnt FROM {table} WHERE LOWER(discovery_source) IN ({placeholders})",
                params,
            )
            total += int(rows[0]["cnt"] or 0)
        except Exception:
            continue
    return total


async def _pending_import_count() -> int:
    networks = await list_monitored_networks(enabled_only=True)
    subnets = [str(n.get("cidr") or "").strip() for n in networks if n.get("cidr")]
    if not subnets:
        return 0
    scans = await db.list_discovery_scans(limit=30)
    if not any(str(s.get("status") or "").lower() == "completed" for s in scans):
        return 0
    inventory = await list_discovery_inventory(subnets=subnets, limit=500, hide_demo=True)
    return sum(
        1
        for d in inventory
        if str(d.get("device_state") or "") in _IMPORTABLE_STATES
        and not d.get("imported_host_id")
        and not int(d.get("ignored") or 0)
    )


async def _settings_reasons() -> list[str]:
    reasons: list[str] = []
    try:
        update = await get_update_status()
        if update.get("update_available"):
            reasons.append("update_available")
    except Exception:
        pass
    try:
        if not list_backups():
            reasons.append("backup_overdue")
    except Exception:
        pass
    try:
        base_url = str(settings.app_base_url or "").lower()
        if settings.is_production and (
            not base_url
            or "localhost" in base_url
            or "127.0.0.1" in base_url
            or base_url.startswith("http://")
        ):
            reasons.append("domain_misconfigured")
    except Exception:
        pass
    if settings.effective_secret_key == "change-me":
        reasons.append("security_warning")
    try:
        logs = await db.list_system_logs(category="notification", limit=5, offset=0)
        if any(str(row.get("event_type") or "") == "notification_failed" for row in logs):
            reasons.append("notification_failed")
    except Exception:
        pass
    return reasons


async def _license_reasons(license_status: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if not license_status.get("write_allowed", True):
        reasons.append("license_expired")
    max_hosts = license_status.get("max_hosts")
    used_hosts = int(license_status.get("used_hosts") or 0)
    if max_hosts and used_hosts > int(max_hosts):
        reasons.append("license_limit_exceeded")
    max_subnets = license_status.get("max_subnets") or license_status.get("max_discovery_subnets")
    used_subnets = int(license_status.get("used_subnets") or 0)
    if max_subnets and used_subnets > int(max_subnets):
        reasons.append("subnet_limit_exceeded")
    return reasons


async def _server_health_reasons() -> list[str]:
    reasons: list[str] = []
    try:
        metrics = collect_system_metrics()
        cpu = metrics.get("cpu_percent")
        ram = metrics.get("ram_percent")
        disk = metrics.get("disk_percent")
        if any(
            v is not None and float(v) >= 90
            for v in (cpu, ram, disk)
        ):
            reasons.append("resource_pressure")
        elif any(
            v is not None and float(v) >= 80
            for v in (cpu, ram, disk)
        ):
            reasons.append("resource_pressure")
    except Exception:
        pass
    try:
        scheduler = await _scheduler_payload()
        if not scheduler.get("running"):
            reasons.append("scheduler_stopped")
    except Exception:
        pass
    try:
        deep = await deep_health()
        if not deep.get("database", {}).get("ok", True):
            reasons.append("database_issue")
        for key in ("data", "backups", "logs"):
            check = deep.get(key) or {}
            if check.get("writable") is False:
                reasons.append("storage_not_writable")
                break
    except Exception:
        pass
    try:
        for path in (settings.data_dir_path, settings.backup_dir_path, settings.log_dir_path):
            status = directory_status(path)
            if status.get("exists") and not status.get("writable"):
                reasons.append("storage_not_writable")
                break
    except Exception:
        pass
    return list(dict.fromkeys(reasons))


async def _network_discovery_reasons() -> list[str]:
    reasons: list[str] = []
    networks = await list_monitored_networks(enabled_only=True)
    if not networks:
        reasons.append("no_monitored_network")
    scans = await db.list_discovery_scans(limit=10)
    if any(str(s.get("status") or "").lower() == "failed" for s in scans):
        reasons.append("scan_failed")
    if await _count_demo_discovery_rows() > 0:
        reasons.append("demo_data_detected")
    if await _pending_import_count() > 0:
        reasons.append("pending_imports")
    return reasons


def _can(user: AuthenticatedUser | str, permission: str) -> bool:
    if isinstance(user, AuthenticatedUser):
        return has_permissions(
            user.role,
            permission,
            explicit_permissions=user.explicit_permissions,
            is_superadmin=user.is_superadmin,
        )
    return has_permissions(str(user or "viewer"), permission)


async def build_navigation_status(user: AuthenticatedUser | str = "viewer") -> dict[str, Any]:
    """Return sidebar badge summary for authenticated users."""
    license_status = await license_service.status()
    payload: dict[str, Any] = {}

    if _can(user, "admin"):
        payload["settings"] = _block(await _settings_reasons())
    else:
        payload["settings"] = _block([])

    if _can(user, "read"):
        payload["license"] = _block(await _license_reasons(license_status))
    else:
        payload["license"] = _block([])

    if _can(user, "system.activity.view"):
        payload["server_health"] = _block(await _server_health_reasons())
    else:
        payload["server_health"] = _block([])

    if _can(user, "read"):
        payload["network_discovery"] = _block(await _network_discovery_reasons())
    else:
        payload["network_discovery"] = _block([])

    if _can(user, "read"):
        try:
            unread = await db.count_unread_notifications()
        except Exception:
            unread = 0
        if unread > 0:
            payload["notifications"] = _block(["unread_notifications"], count=unread)
        else:
            payload["notifications"] = _block([])
    else:
        payload["notifications"] = _block([])

    # Touch activity service so import stays wired for future live checks.
    _ = activity_service.checks_running_count()
    return payload
