"""Extended employee CRUD for the HR attendance module."""

from __future__ import annotations

from typing import Any

from ditaknet import database as db
from ditaknet.core.employee_presence import create_privacy_audit_log, list_employee_devices
from ditaknet.core.hr.access import enforce_hr_access

EMPLOYMENT_STATUSES = {"active", "inactive", "suspended"}


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _row(row: Any) -> dict[str, Any]:
    return dict(row)


async def create_employee(
    *,
    full_name: str,
    department_id: int | None = None,
    group_id: int | None = None,
    default_shift_id: int | None = None,
    position: str = "",
    email: str = "",
    phone: str = "",
    employee_code: str = "",
    employment_status: str = "active",
    hire_date: str = "",
    notes: str = "",
    privacy_notice_accepted: bool = False,
    actor: str = "system",
) -> dict[str, Any]:
    await enforce_hr_access()
    status = employment_status if employment_status in EMPLOYMENT_STATUSES else "active"
    conn = await db.get_db()
    cursor = await conn.execute(
        """INSERT INTO employees
           (full_name, department, position, email, phone, employee_code, status,
            privacy_notice_accepted, department_id, group_id, default_shift_id,
            employment_status, hire_date, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            full_name.strip(),
            "",
            position.strip(),
            email.strip(),
            phone.strip(),
            employee_code.strip(),
            "active" if status == "active" else "inactive",
            int(privacy_notice_accepted),
            department_id,
            group_id,
            default_shift_id,
            status,
            hire_date.strip() or None,
            notes.strip(),
            _now(),
        ),
    )
    await conn.commit()
    employee = await get_employee(cursor.lastrowid)
    await create_privacy_audit_log(
        actor_user_id=actor,
        action="employee_created",
        target_employee_id=cursor.lastrowid,
        details=full_name.strip(),
    )
    return employee or {}


async def get_employee(employee_id: int) -> dict[str, Any] | None:
    await enforce_hr_access()
    conn = await db.get_db()
    rows = await conn.execute_fetchall(
        """
        SELECT e.*,
               d.name AS department_name,
               g.name AS group_name,
               o.name AS default_office_name,
               o.code AS default_office_code
        FROM employees e
        LEFT JOIN departments d ON d.id = e.department_id
        LEFT JOIN employee_groups g ON g.id = e.group_id
        LEFT JOIN offices o ON o.id = e.default_office_id
        WHERE e.id = ?
        """,
        (employee_id,),
    )
    return _row(rows[0]) if rows else None


async def list_employees(
    *,
    search: str = "",
    department_id: int | None = None,
    group_id: int | None = None,
    employment_status: str = "",
    department_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    await enforce_hr_access()
    conn = await db.get_db()
    conditions: list[str] = []
    params: list[Any] = []
    if search:
        conditions.append("(e.full_name LIKE ? OR e.email LIKE ? OR e.employee_code LIKE ?)")
        value = f"%{search}%"
        params.extend([value, value, value])
    if department_id is not None:
        conditions.append("e.department_id = ?")
        params.append(department_id)
    if group_id is not None:
        conditions.append("e.group_id = ?")
        params.append(group_id)
    if employment_status:
        conditions.append("COALESCE(e.employment_status, e.status) = ?")
        params.append(employment_status)
    if department_ids is not None:
        if not department_ids:
            return []
        placeholders = ",".join("?" for _ in department_ids)
        conditions.append(f"e.department_id IN ({placeholders})")
        params.extend(department_ids)
    query = """
        SELECT e.*,
               d.name AS department_name,
               g.name AS group_name,
               o.name AS default_office_name,
               o.code AS default_office_code
        FROM employees e
        LEFT JOIN departments d ON d.id = e.department_id
        LEFT JOIN employee_groups g ON g.id = e.group_id
        LEFT JOIN offices o ON o.id = e.default_office_id
    """
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY e.full_name"
    rows = await conn.execute_fetchall(query, params)
    return [_row(r) for r in rows]


async def update_employee(employee_id: int, *, actor: str = "system", **fields: Any) -> dict[str, Any] | None:
    await enforce_hr_access()
    allowed = (
        "full_name",
        "department_id",
        "group_id",
        "default_shift_id",
        "position",
        "email",
        "phone",
        "employee_code",
        "employment_status",
        "hire_date",
        "notes",
        "privacy_notice_accepted",
    )
    sets: list[str] = []
    values: list[Any] = []
    for key in allowed:
        if key in fields and fields[key] is not None:
            value = fields[key]
            if key == "privacy_notice_accepted":
                value = int(bool(value))
            if key == "employment_status":
                if value not in EMPLOYMENT_STATUSES:
                    value = "active"
                sets.append("status = ?")
                values.append("active" if value == "active" else "inactive")
            sets.append(f"{key} = ?")
            values.append(value)
    if not sets:
        return await get_employee(employee_id)
    sets.append("updated_at = ?")
    values.extend([_now(), employee_id])
    conn = await db.get_db()
    await conn.execute(f"UPDATE employees SET {', '.join(sets)} WHERE id = ?", values)
    await conn.commit()
    await create_privacy_audit_log(
        actor_user_id=actor,
        action="employee_updated",
        target_employee_id=employee_id,
        details=",".join(sets),
    )
    return await get_employee(employee_id)


async def get_employee_with_devices(employee_id: int) -> dict[str, Any] | None:
    employee = await get_employee(employee_id)
    if not employee:
        return None
    employee["devices"] = await list_employee_devices(employee_id)
    return employee
