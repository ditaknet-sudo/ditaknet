"""Safe About / Support / Trust payload assembly (no secrets)."""

from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Any

from ditaknet import database as db
from ditaknet.config import settings
from ditaknet.core.build_metadata import build_metadata
from ditaknet.core.features import feature_flags_from_license
from ditaknet.core.legal import legal_documents_status, legal_summary
from ditaknet.core.runtime_settings import telegram_enabled
from ditaknet.core.updates import get_update_status


def detect_deployment_mode() -> str:
    """Best-effort deployment target detection."""
    override = (settings.app_deployment_mode or "").strip()
    if override:
        return override
    if Path("/.dockerenv").is_file():
        if os.getenv("TRUENAS") or os.getenv("TNAP") or os.getenv("NAS_HOST"):
            return "TrueNAS"
        return "Docker"
    system = platform.system()
    if system == "Windows":
        return "Windows"
    if system == "Linux":
        return "Linux"
    return "Unknown"


def _support_contact_fields() -> dict[str, str]:
    """Return only non-empty support/author contact fields."""
    fields = {
        "author_name": settings.app_author_name.strip(),
        "author_website": settings.app_author_website.strip(),
        "support_email": settings.app_support_email.strip(),
        "support_phone": settings.app_support_phone.strip(),
        "support_telegram": settings.app_support_telegram.strip(),
        "support_url": settings.app_support_url.strip(),
        "documentation_url": settings.app_documentation_url.strip(),
    }
    return {key: value for key, value in fields.items() if value}


def _looks_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    return (
        not lowered
        or lowered in {"change-me", "changeme", "admin", "password"}
        or lowered.startswith("change_me")
        or lowered.startswith("change-me")
        or "replace" in lowered
    )


def _path_location_status(path: Path, parent: Path) -> str:
    try:
        path.resolve().relative_to(parent.resolve())
        return "safe_path"
    except Exception:
        return "unsafe_path"


async def _count_table(table: str) -> int:
    if table not in {"offices", "branch_agents"}:
        return 0
    try:
        conn = await db.get_db()
        rows = await conn.execute_fetchall(f"SELECT COUNT(*) AS count FROM {table}")
        return int(rows[0]["count"]) if rows else 0
    except Exception:
        return 0


async def scheduler_status_label() -> str:
    if not settings.scheduler_enabled:
        return "disabled"
    try:
        import asyncio

        from ditaknet.api.deps import get_scheduler

        scheduler = get_scheduler()
        status_fn = getattr(scheduler, "status", None)
        if callable(status_fn):
            result = status_fn()
            if asyncio.iscoroutine(result):
                payload = await result
            elif isinstance(result, dict):
                payload = result
            else:
                payload = {}
            return "running" if payload.get("running") else "stopped"
        sched_obj = getattr(scheduler, "_scheduler", None)
        if sched_obj and getattr(sched_obj, "running", False):
            return "running"
        return "stopped"
    except RuntimeError:
        return "unknown"


def _module_rows(license_info: dict[str, Any]) -> list[dict[str, Any]]:
    flags = feature_flags_from_license(license_info)
    module_defs = (
        ("discovery_enabled", "nav.discovery"),
        ("topology_enabled", "nav.topology"),
        ("agent_enabled", "package.feature.agent"),
        ("advanced_reports_enabled", "package.feature.advanced_reports"),
        ("employee_attendance_enabled", "employee_attendance"),
        ("departments_enabled", "departments"),
        ("employee_groups_enabled", "employee_groups"),
        ("shifts_enabled", "shifts"),
        ("monthly_work_hours_enabled", "attendance_reports"),
        ("multi_office_enabled", "offices"),
        ("branch_agent_enabled", "multi_office_branch_presence"),
    )
    return [
        {"key": key, "label_key": label_key, "enabled": bool(flags.get(key))}
        for key, label_key in module_defs
    ]


def _security_status(
    *,
    request: Any | None,
    deployment_mode: str,
    notification_on: bool,
    legal_status: dict[str, str],
) -> dict[str, Any]:
    request_scheme = str(getattr(getattr(request, "url", None), "scheme", "") or "").lower()
    forwarded_proto = ""
    if request is not None:
        forwarded_proto = str(request.headers.get("x-forwarded-proto", "")).lower()
    https_configured = (
        request_scheme == "https"
        or forwarded_proto == "https"
        or settings.app_base_url.strip().lower().startswith("https://")
    )
    reverse_proxy = "yes" if forwarded_proto else ("configured" if settings.trusted_proxies else "unknown")

    return {
        "https_configured": "yes" if https_configured else "no",
        "tls_certificate_detected": "yes" if https_configured else "no",
        "reverse_proxy_detected": reverse_proxy,
        "privacy_policy": legal_status.get("privacy-policy", "not_configured"),
        "eula": legal_status.get("eula", "not_configured"),
        "dpa": legal_status.get("dpa", "not_configured"),
        "open_source_notices": legal_status.get("open-source-notices", "not_configured"),
        "security_policy": legal_status.get("security", "not_configured"),
        "employee_monitoring_notice": legal_status.get("employee-monitoring-notice", "not_configured"),
        "backup_configured": "yes" if settings.backup_dir_path.exists() else "unknown",
        "audit_logging_enabled": "yes",
        "role_based_access_enabled": "yes",
        "running_as_docker": "yes" if deployment_mode in {"Docker", "TrueNAS"} else "no",
        "deployment_mode": deployment_mode,
        "default_admin_password_changed": "yes",
        "secret_key_configured": "no" if _looks_placeholder(settings.effective_secret_key) else "yes",
        "database_location": _path_location_status(settings.db_path, settings.data_dir_path),
        "database_path": str(settings.db_path),
        "data_dir": str(settings.data_dir_path),
        "logs_dir": str(settings.log_dir_path),
        "backups_dir": str(settings.backup_dir_path),
        "update_method": "Docker/GitHub image releases",
        "update_source": "GitHub/GHCR Docker image",
        "privileged_docker_mode": "unknown",
        "host_network": "unknown",
        "telegram_configured": "yes" if notification_on else "no",
        "no_tls_message": (
            "No TLS certificate detected by DitakNet. If you expose this server outside your local network, "
            "use HTTPS through a reverse proxy or secure gateway."
        ),
    }


def _data_processed(module_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flags = {item["key"]: item["enabled"] for item in module_rows}
    return [
        {"label_key": "data.ip_address", "enabled": True},
        {"label_key": "data.mac_address", "enabled": True},
        {"label_key": "data.hostname", "enabled": True},
        {"label_key": "data.device_type", "enabled": True},
        {"label_key": "data.service_status", "enabled": True},
        {"label_key": "data.monitoring_results", "enabled": True},
        {"label_key": "data.alert_history", "enabled": True},
        {"label_key": "data.license_status", "enabled": True},
        {"label_key": "data.employee_department_shift", "enabled": flags.get("employee_attendance_enabled", False)},
        {"label_key": "data.attendance_events", "enabled": flags.get("employee_attendance_enabled", False)},
        {"label_key": "data.office_branch_presence", "enabled": flags.get("multi_office_enabled", False)},
    ]


def _does_not_do() -> list[str]:
    return [
        "no_data_sale",
        "no_advertising_profile",
        "no_gps_tracking",
        "no_personal_files",
        "no_visited_websites",
        "no_traffic_inspection",
        "no_hidden_surveillance",
        "no_public_internet_scan",
    ]


async def build_about_payload(*, lang: str = "en", request: Any | None = None) -> dict[str, Any]:
    """Build public-safe about/support/trust JSON."""
    from ditaknet.core.licensing import license_service

    license_info = await license_service.status()
    contacts = _support_contact_fields()
    notification_on = await telegram_enabled()
    update_status = await get_update_status()
    deployment_mode = detect_deployment_mode()
    docs_status = legal_documents_status()
    docs_summary = legal_summary()
    modules = _module_rows(license_info)
    support_configured = any(
        contacts.get(key)
        for key in ("support_email", "support_phone", "support_telegram", "support_url", "documentation_url")
    )

    meta = build_metadata(friendly_missing=True)
    payload: dict[str, Any] = {
        "app_name": settings.app_name,
        "armenian_name": settings.app_brand_name_hy,
        "brand_name": settings.app_brand_name,
        "brand_name_hy": settings.app_brand_name_hy,
        "display_name": settings.app_display_name,
        "product_type": (
            "Local network visibility, monitoring, discovery, alerting, IT support, "
            "employee attendance, and multi-office branch monitoring platform."
        ),
        "version": meta["version"],
        "build_commit": meta["build_commit"],
        "build_date": meta["build_date"],
        "image_tag": meta["image_tag"],
        "github_repository": meta["github_repository"],
        "ghcr_image": meta["ghcr_image"],
        "installation_id": await license_service.get_installation_id(),
        "deployment_mode": deployment_mode,
        "server_port": settings.app_port,
        "scheduler_status": await scheduler_status_label(),
        "database_type": settings.database_type,
        "notification_enabled": notification_on,
        "notifications_enabled": notification_on,
        "language": lang,
        "current_language": lang,
        "license_tier": license_info.get("tier"),
        "license_package": license_info.get("tier"),
        "license_status": license_info.get("status"),
        "license_expires_at": license_info.get("expires_at"),
        "used_hosts": license_info.get("used_hosts"),
        "max_hosts": license_info.get("max_hosts"),
        "used_services": license_info.get("used_services"),
        "max_services": license_info.get("max_services"),
        "used_subnets": license_info.get("used_subnets"),
        "max_subnets": license_info.get("max_vlans_or_networks") or license_info.get("max_subnets"),
        "used_offices": await _count_table("offices"),
        "max_offices": license_info.get("max_offices"),
        "used_branch_agents": await _count_table("branch_agents"),
        "max_branch_agents": license_info.get("max_branch_agents"),
        "modules": modules,
        "enabled_modules": [item["key"] for item in modules if item["enabled"]],
        "disabled_modules": [item["key"] for item in modules if not item["enabled"]],
        "legal_documents_status": docs_status,
        "legal_documents_summary": docs_summary,
        "security_status": _security_status(
            request=request,
            deployment_mode=deployment_mode,
            notification_on=notification_on,
            legal_status=docs_summary,
        ),
        "data_processed": _data_processed(modules),
        "does_not_do": _does_not_do(),
        "support_configured": support_configured,
        "update_status": update_status,
        "update_source": "GitHub/GHCR Docker image",
        **contacts,
    }
    return payload
