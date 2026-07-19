"""License-aware feature flags and web navigation."""

from __future__ import annotations

from typing import Any

from ditaknet.core.packages import PACKAGE_PROFESSIONAL
from ditaknet.security import has_hr_permission, has_office_permission, has_permissions

NavItem = dict[str, Any]


def _enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() not in {"", "0", "false", "none", "no"}


def feature_flags_from_license(status: dict[str, Any] | None) -> dict[str, bool]:
    """Build stable feature flags from the current license status."""
    status = status or {}
    employee_attendance = _enabled(status.get("employee_presence_enabled"))
    multi_office = employee_attendance and _enabled(status.get("multi_office_enabled"))
    reports = str(status.get("reports_enabled") or "").lower()
    topology = status.get("topology_enabled")

    return {
        "discovery_enabled": True,
        "topology_enabled": _enabled(topology) or str(topology).lower() in {"basic", "advanced"},
        "agent_enabled": _enabled(status.get("agent_enabled")),
        "advanced_reports_enabled": reports == "advanced",
        "employee_attendance_enabled": employee_attendance,
        "departments_enabled": employee_attendance,
        "employee_groups_enabled": employee_attendance,
        "shifts_enabled": employee_attendance,
        "monthly_work_hours_enabled": employee_attendance,
        "multi_office_enabled": multi_office,
        "branch_agent_enabled": multi_office and _enabled(status.get("branch_agent_enabled")),
    }


def _item_visible(item: NavItem, flags: dict[str, bool], role: str) -> bool:
    feature = item.get("feature")
    if feature and not flags.get(str(feature), False):
        return False
    permission = item.get("permission")
    if permission and not has_permissions(role, str(permission)):
        return False
    hr_permission = item.get("hr_permission")
    if hr_permission and not has_hr_permission(role, str(hr_permission)):
        return False
    office_permission = item.get("office_permission")
    if office_permission and not has_office_permission(role, str(office_permission)):
        return False
    return True


def _filter_items(items: list[NavItem], flags: dict[str, bool], role: str) -> list[NavItem]:
    return [item for item in items if _item_visible(item, flags, role)]


def _section(
    section_id: str,
    label_key: str,
    items: list[NavItem],
    flags: dict[str, bool],
    role: str,
) -> dict[str, Any] | None:
    visible = _filter_items(items, flags, role)
    if not visible:
        return None
    return {"id": section_id, "label_key": label_key, "items": visible}


def _package_label_key(tier: str) -> str:
    return "license.complimentary.badge"


def _corporate_routes() -> set[str]:
    return {
        "/employees",
        "/departments",
        "/employee-groups",
        "/shifts",
        "/attendance",
        "/attendance/reports/monthly",
        "/attendance/settings",
    }


def web_navigation(status: dict[str, Any] | None, role: str) -> dict[str, Any]:
    """Return grouped sidebar navigation for this license and role."""
    status = status or {}
    flags = feature_flags_from_license(status)
    role = role or "viewer"

    sections: list[dict[str, Any]] = []
    for block in (
        _section(
            "overview",
            "nav.overview",
            [
                {
                    "route": "/dashboard",
                    "match": "/dashboard",
                    "exact_paths": ["/", "/dashboard"],
                    "icon": "bi-speedometer2",
                    "label_key": "nav.dashboard",
                },
                {
                    "route": "/system/activity",
                    "match": "/system/activity",
                    "icon": "bi-heart-pulse",
                    "label_key": "nav.server_health",
                    "permission": "system.activity.view",
                    "nav_status_key": "server_health",
                },
            ],
            flags,
            role,
        ),
        _section(
            "monitoring",
            "nav.monitoring",
            [
                {"route": "/devices", "match": "/devices", "icon": "bi-diagram-3", "label_key": "nav.devices"},
                {"route": "/hosts", "match": "/hosts", "icon": "bi-hdd-network", "label_key": "nav.hosts"},
                {"route": "/services", "match": "/services", "icon": "bi-activity", "label_key": "nav.services"},
                {"route": "/alerts", "match": "/alerts", "icon": "bi-bell", "label_key": "nav.alerts"},
                {"route": "/results", "match": "/results", "icon": "bi-list-check", "label_key": "nav.results"},
            ],
            flags,
            role,
        ),
        _section(
            "discovery",
            "nav.discovery",
            [
                {
                    "route": "/discovery",
                    "match": "/discovery",
                    "icon": "bi-search",
                    "label_key": "nav.network_discovery",
                    "nav_status_key": "network_discovery",
                },
                {
                    "route": "/topology",
                    "match": "/topology",
                    "icon": "bi-diagram-2",
                    "label_key": "nav.topology",
                    "feature": "topology_enabled",
                },
            ],
            flags,
            role,
        ),
        _section(
            "operations",
            "nav.operations",
            [
                {
                    "route": "/maintenance",
                    "match": "/maintenance",
                    "icon": "bi-tools",
                    "label_key": "nav.maintenance",
                },
                {
                    "route": "/settings/backups",
                    "match": "/settings/backups",
                    "icon": "bi-database-check",
                    "label_key": "nav.backups",
                    "permission": "admin",
                },
                {
                    "route": "/system/logs",
                    "match": "/system/logs",
                    "icon": "bi-journal-text",
                    "label_key": "nav.system_logs",
                    "permission": "system.logs.view",
                },
            ],
            flags,
            role,
        ),
        _section(
            "corporate",
            "nav.corporate",
            [
                {
                    "route": "/employees",
                    "match": "/employees",
                    "icon": "bi-people",
                    "label_key": "nav.employees",
                    "feature": "employee_attendance_enabled",
                    "hr_permission": "hr.view",
                },
                {
                    "route": "/attendance",
                    "match": "/attendance",
                    "exact_paths": ["/attendance"],
                    "icon": "bi-calendar-check",
                    "label_key": "nav.attendance",
                    "feature": "employee_attendance_enabled",
                    "hr_permission": "hr.view_attendance",
                },
                {
                    "route": "/departments",
                    "match": "/departments",
                    "icon": "bi-building",
                    "label_key": "nav.departments",
                    "feature": "departments_enabled",
                    "hr_permission": "hr.view",
                },
                {
                    "route": "/employee-groups",
                    "match": "/employee-groups",
                    "icon": "bi-collection",
                    "label_key": "nav.employee_groups",
                    "feature": "employee_groups_enabled",
                    "hr_permission": "hr.view",
                },
                {
                    "route": "/shifts",
                    "match": "/shifts",
                    "icon": "bi-clock",
                    "label_key": "nav.shifts",
                    "feature": "shifts_enabled",
                    "hr_permission": "hr.view",
                },
                {
                    "route": "/attendance/reports/monthly",
                    "match": "/attendance/reports",
                    "icon": "bi-file-earmark-bar-graph",
                    "label_key": "nav.attendance_reports",
                    "feature": "monthly_work_hours_enabled",
                    "hr_permission": "hr.view_attendance",
                },
            ],
            flags,
            role,
        ),
        _section(
            "multi_office",
            "nav.multi_office",
            [
                {
                    "route": "/offices",
                    "match": "/offices",
                    "icon": "bi-geo-alt",
                    "label_key": "nav.offices_branches",
                    "feature": "multi_office_enabled",
                    "office_permission": "offices.view",
                },
            ],
            flags,
            role,
        ),
        _section(
            "administration",
            "nav.administration",
            [
                {
                    "route": "/settings",
                    "match": "/settings",
                    "icon": "bi-gear",
                    "label_key": "nav.settings",
                    "nav_status_key": "settings",
                },
                {
                    "route": "/license",
                    "match": "/license",
                    "icon": "bi-gift",
                    "label_key": "nav.license",
                    "permission": "read",
                    "nav_status_key": "license",
                },
                {
                    "route": "/notifications",
                    "match": "/notifications",
                    "icon": "bi-bell-fill",
                    "label_key": "nav.notifications",
                    "permission": "read",
                    "nav_status_key": "notifications",
                },
                {"route": "/about", "match": "/about", "icon": "bi-info-circle", "label_key": "nav.about_support"},
            ],
            flags,
            role,
        ),
    ):
        if block:
            sections.append(block)

    flat_items = [item for section in sections for item in section["items"]]
    corporate = _corporate_routes()

    return {
        "feature_flags": flags,
        "sidebar_sections": sections,
        "sidebar_meta": {
            "tier": str(status.get("tier") or PACKAGE_PROFESSIONAL),
            "package_label_key": _package_label_key(
                str(status.get("tier") or PACKAGE_PROFESSIONAL)
            ),
        },
        "main_menu": flat_items,
        "attendance_menu": [item for item in flat_items if item.get("route") in corporate],
    }
