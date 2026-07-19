"""HR module license gating and department-scoped visibility."""

from __future__ import annotations

from typing import Any

from ditaknet import database as db
from ditaknet.core.licensing import license_service
from ditaknet.security import AuthenticatedUser, has_hr_permission


async def enforce_hr_access() -> None:
    await license_service.enforce_employee_presence_access()


async def get_user_department_scope(user: AuthenticatedUser) -> list[int] | None:
    """Return allowed department IDs for department managers; None means all."""
    if user.role == "admin" or user.role == "hr_manager":
        return None
    if user.role != "department_manager":
        return None
    conn = await db.get_db()
    rows = await conn.execute_fetchall(
        "SELECT id FROM departments WHERE manager_user_id = ? AND is_active = 1",
        (user.username,),
    )
    return [int(row["id"]) for row in rows]


def user_can_view_sensitive_network(user: AuthenticatedUser) -> bool:
    return user.role in {"admin", "hr_manager"} or has_hr_permission(
        user.role, "hr.manage_employees"
    )


async def filter_employees_by_scope(
    employees: list[dict[str, Any]],
    user: AuthenticatedUser,
) -> list[dict[str, Any]]:
    scope = await get_user_department_scope(user)
    if scope is None:
        return employees
    allowed = set(scope)
    return [e for e in employees if e.get("department_id") in allowed]


async def assert_employee_in_scope(employee_id: int, user: AuthenticatedUser) -> None:
    scope = await get_user_department_scope(user)
    if scope is None:
        return
    employee = await _get_employee_department_id(employee_id)
    if employee is None:
        raise ValueError("Employee not found")
    if employee not in scope:
        raise PermissionError("Employee not in your department scope")


async def _get_employee_department_id(employee_id: int) -> int | None:
    conn = await db.get_db()
    rows = await conn.execute_fetchall(
        "SELECT department_id FROM employees WHERE id = ?", (employee_id,)
    )
    if not rows:
        return None
    value = rows[0]["department_id"]
    return int(value) if value is not None else None
