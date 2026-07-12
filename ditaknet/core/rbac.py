"""Role and permission catalog for DitakNet.

The catalog is intentionally centralized so API dependencies, database seeds,
and the admin UI all enforce the same permission names.
"""

from __future__ import annotations

from dataclasses import dataclass


PERMISSION_GROUPS: dict[str, list[tuple[str, str]]] = {
    "Dashboard": [
        ("dashboard.view", "View dashboard"),
    ],
    "Devices": [
        ("devices.view", "View devices"),
        ("devices.create", "Create devices"),
        ("devices.edit", "Edit devices"),
        ("devices.delete", "Delete devices"),
        ("devices.run_check", "Run device checks"),
    ],
    "Discovery": [
        ("discovery.view", "View discovery"),
        ("discovery.scan", "Run discovery scans"),
        ("discovery.import", "Import discovered devices"),
        ("discovery.manage_networks", "Manage VLANs/subnets"),
    ],
    "Monitoring": [
        ("services.view", "View services"),
        ("services.create", "Create services"),
        ("services.edit", "Edit services"),
        ("services.delete", "Delete services"),
        ("alerts.view", "View alerts"),
        ("alerts.acknowledge", "Acknowledge alerts"),
        ("results.view", "View results"),
    ],
    "System": [
        ("system.health.view", "View server health"),
        ("system.logs.view", "View system logs"),
        ("system.activity.view", "View system activity"),
    ],
    "Settings": [
        ("settings.view", "View settings"),
        ("settings.edit", "Edit settings"),
        ("settings.security", "Manage security settings"),
        ("settings.domain", "Manage domain settings"),
        ("settings.updates", "Manage updates"),
    ],
    "Backups": [
        ("backups.view", "View backups"),
        ("backups.create", "Create backups"),
        ("backups.download", "Download backups"),
        ("backups.restore", "Restore backups"),
        ("backups.delete", "Delete backups"),
    ],
    "License": [
        ("license.view", "View license"),
        ("license.manage", "Manage license"),
    ],
    "Users": [
        ("users.view", "View users"),
        ("users.create", "Create users"),
        ("users.edit", "Edit users"),
        ("users.disable", "Disable users"),
        ("users.reset_password", "Reset user passwords"),
        ("users.manage_roles", "Manage roles and permissions"),
    ],
    "Corporate": [
        ("employees.view", "View employees"),
        ("employees.create", "Create employees"),
        ("employees.edit", "Edit employees"),
        ("attendance.view", "View attendance"),
        ("attendance.edit", "Edit attendance"),
        ("departments.manage", "Manage departments"),
        ("shifts.manage", "Manage shifts"),
    ],
    "Multi-office": [
        ("offices.view", "View offices"),
        ("offices.manage", "Manage offices"),
        ("branch_agents.view", "View branch agents"),
        ("branch_agents.manage", "Manage branch agents"),
    ],
}


LEGACY_PERMISSIONS: set[str] = {
    "read",
    "operate",
    "admin",
    "hr.view",
    "hr.manage_employees",
    "hr.manage_departments",
    "hr.manage_groups",
    "hr.manage_shifts",
    "hr.view_attendance",
    "hr.edit_attendance",
    "hr.export_attendance_reports",
    "hr.manage_attendance_settings",
    "attendance.view_all_offices",
    "attendance.view_assigned_office",
    "offices.view",
    "offices.manage",
    "branches.manage_tokens",
}


ALL_PERMISSIONS: set[str] = {
    permission
    for group in PERMISSION_GROUPS.values()
    for permission, _label in group
} | LEGACY_PERMISSIONS


READ_PERMISSIONS: set[str] = {
    "read",
    "dashboard.view",
    "devices.view",
    "services.view",
    "alerts.view",
    "results.view",
    "discovery.view",
    "system.health.view",
}


ADMIN_PERMISSIONS: set[str] = ALL_PERMISSIONS - {"license.manage"}


ROLE_ALIASES: dict[str, str] = {
    "superadmin": "super_admin",
    "super-admin": "super_admin",
    "super_admin": "super_admin",
    "administrator": "admin",
    "department_manager": "branch_manager",
}


@dataclass(frozen=True)
class RoleDefinition:
    code: str
    name: str
    description: str
    permissions: set[str]
    is_system: bool = True
    license_feature: str = ""


DEFAULT_ROLES: dict[str, RoleDefinition] = {
    "super_admin": RoleDefinition(
        code="super_admin",
        name="Super Admin",
        description="Full owner access, including users, license, restore, and security controls.",
        permissions=ALL_PERMISSIONS | {"license.manage"},
    ),
    "admin": RoleDefinition(
        code="admin",
        name="Admin",
        description="Operational administrator without private license-signing access.",
        permissions=ADMIN_PERMISSIONS | {"admin"},
    ),
    "operator": RoleDefinition(
        code="operator",
        name="Operator",
        description="Can run monitoring, checks, scans, and acknowledge alerts.",
        permissions=READ_PERMISSIONS
        | {
            "operate",
            "devices.edit",
            "devices.run_check",
            "discovery.scan",
            "discovery.import",
            "services.edit",
            "alerts.acknowledge",
        },
    ),
    "viewer": RoleDefinition(
        code="viewer",
        name="Viewer",
        description="Read-only visibility for dashboards, devices, alerts, and results.",
        permissions=READ_PERMISSIONS,
    ),
    "hr_manager": RoleDefinition(
        code="hr_manager",
        name="HR / Attendance Manager",
        description="Corporate attendance and employee management role.",
        license_feature="employee_attendance",
        permissions=READ_PERMISSIONS
        | {
            "employees.view",
            "employees.create",
            "employees.edit",
            "attendance.view",
            "attendance.edit",
            "departments.manage",
            "shifts.manage",
            "hr.view",
            "hr.manage_employees",
            "hr.manage_departments",
            "hr.manage_groups",
            "hr.manage_shifts",
            "hr.view_attendance",
            "hr.edit_attendance",
            "hr.export_attendance_reports",
            "hr.manage_attendance_settings",
        },
    ),
    "branch_manager": RoleDefinition(
        code="branch_manager",
        name="Branch Manager",
        description="Multi-office branch visibility and branch-agent operations.",
        license_feature="multi_office",
        permissions=READ_PERMISSIONS
        | {
            "offices.view",
            "branch_agents.view",
            "attendance.view_assigned_office",
        },
    ),
}


def normalize_role(role: str | None) -> str:
    value = str(role or "viewer").strip().lower()
    return ROLE_ALIASES.get(value, value if value in DEFAULT_ROLES else "viewer")


def permissions_for_role(role: str | None) -> set[str]:
    return set(DEFAULT_ROLES.get(normalize_role(role), DEFAULT_ROLES["viewer"]).permissions)


def public_permission_groups() -> dict[str, list[dict[str, str]]]:
    return {
        group: [{"code": code, "label": label} for code, label in items]
        for group, items in PERMISSION_GROUPS.items()
    }
