"""Departments, employee groups, shifts, and shift resolution."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ditaknet import database as db
from ditaknet.core.employee_presence import create_privacy_audit_log
from ditaknet.core.hr.access import enforce_hr_access

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row(row: Any) -> dict[str, Any]:
    return dict(row)


def _parse_time(value: str) -> time:
    parts = str(value).strip().split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    return time(hour=hour, minute=minute)


# ─── Departments ──────────────────────────────────────────


async def create_department(
    *,
    name: str,
    description: str = "",
    manager_user_id: str = "",
    is_active: bool = True,
    actor: str = "system",
) -> dict[str, Any]:
    await enforce_hr_access()
    conn = await db.get_db()
    cursor = await conn.execute(
        """INSERT INTO departments (name, description, manager_user_id, is_active, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (name.strip(), description.strip(), manager_user_id.strip(), int(is_active), _now()),
    )
    await conn.commit()
    dept = await get_department(cursor.lastrowid)
    await create_privacy_audit_log(actor_user_id=actor, action="department_created", details=name)
    return dept or {}


async def get_department(department_id: int) -> dict[str, Any] | None:
    await enforce_hr_access()
    conn = await db.get_db()
    rows = await conn.execute_fetchall("SELECT * FROM departments WHERE id = ?", (department_id,))
    return _row(rows[0]) if rows else None


async def list_departments(*, active_only: bool = False) -> list[dict[str, Any]]:
    await enforce_hr_access()
    conn = await db.get_db()
    query = "SELECT * FROM departments"
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY name"
    rows = await conn.execute_fetchall(query)
    return [_row(r) for r in rows]


async def update_department(department_id: int, *, actor: str = "system", **fields: Any) -> dict[str, Any] | None:
    await enforce_hr_access()
    allowed = ("name", "description", "manager_user_id", "is_active")
    sets: list[str] = []
    values: list[Any] = []
    for key in allowed:
        if key in fields and fields[key] is not None:
            value = fields[key]
            if key == "is_active":
                value = int(bool(value))
            sets.append(f"{key} = ?")
            values.append(value)
    if not sets:
        return await get_department(department_id)
    sets.append("updated_at = ?")
    values.extend([_now(), department_id])
    conn = await db.get_db()
    await conn.execute(f"UPDATE departments SET {', '.join(sets)} WHERE id = ?", values)
    await conn.commit()
    await create_privacy_audit_log(actor_user_id=actor, action="department_updated", details=str(department_id))
    return await get_department(department_id)


# ─── Employee groups ──────────────────────────────────────


async def create_employee_group(
    *,
    name: str,
    description: str = "",
    department_id: int | None = None,
    default_shift_id: int | None = None,
    is_active: bool = True,
    actor: str = "system",
) -> dict[str, Any]:
    await enforce_hr_access()
    conn = await db.get_db()
    cursor = await conn.execute(
        """INSERT INTO employee_groups
           (name, description, department_id, default_shift_id, is_active, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (name.strip(), description.strip(), department_id, default_shift_id, int(is_active), _now()),
    )
    await conn.commit()
    group = await get_employee_group(cursor.lastrowid)
    await create_privacy_audit_log(actor_user_id=actor, action="group_created", details=name)
    return group or {}


async def get_employee_group(group_id: int) -> dict[str, Any] | None:
    await enforce_hr_access()
    conn = await db.get_db()
    rows = await conn.execute_fetchall("SELECT * FROM employee_groups WHERE id = ?", (group_id,))
    return _row(rows[0]) if rows else None


async def list_employee_groups(
    *,
    department_id: int | None = None,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    await enforce_hr_access()
    conn = await db.get_db()
    conditions: list[str] = []
    params: list[Any] = []
    if department_id is not None:
        conditions.append("department_id = ?")
        params.append(department_id)
    if active_only:
        conditions.append("is_active = 1")
    query = "SELECT * FROM employee_groups"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY name"
    rows = await conn.execute_fetchall(query, params)
    return [_row(r) for r in rows]


async def update_employee_group(group_id: int, *, actor: str = "system", **fields: Any) -> dict[str, Any] | None:
    await enforce_hr_access()
    allowed = ("name", "description", "department_id", "default_shift_id", "is_active")
    sets: list[str] = []
    values: list[Any] = []
    for key in allowed:
        if key in fields and fields[key] is not None:
            value = fields[key]
            if key == "is_active":
                value = int(bool(value))
            sets.append(f"{key} = ?")
            values.append(value)
    if not sets:
        return await get_employee_group(group_id)
    sets.append("updated_at = ?")
    values.extend([_now(), group_id])
    conn = await db.get_db()
    await conn.execute(f"UPDATE employee_groups SET {', '.join(sets)} WHERE id = ?", values)
    await conn.commit()
    await create_privacy_audit_log(actor_user_id=actor, action="group_updated", details=str(group_id))
    return await get_employee_group(group_id)


# ─── Shifts ───────────────────────────────────────────────


async def create_shift(
    *,
    name: str,
    start_time: str,
    end_time: str,
    timezone: str = "UTC",
    break_minutes: int = 0,
    grace_late_minutes: int = 10,
    grace_leave_early_minutes: int = 10,
    expected_work_minutes: int | None = None,
    color: str = "",
    is_overnight: bool = False,
    is_active: bool = True,
    actor: str = "system",
) -> dict[str, Any]:
    await enforce_hr_access()
    if expected_work_minutes is None:
        expected_work_minutes = _default_expected_minutes(start_time, end_time, break_minutes, is_overnight)
    conn = await db.get_db()
    cursor = await conn.execute(
        """INSERT INTO shifts
           (name, start_time, end_time, timezone, break_minutes, grace_late_minutes,
            grace_leave_early_minutes, expected_work_minutes, color, is_overnight,
            is_active, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            name.strip(),
            start_time,
            end_time,
            timezone,
            break_minutes,
            grace_late_minutes,
            grace_leave_early_minutes,
            expected_work_minutes,
            color,
            int(is_overnight),
            int(is_active),
            _now(),
        ),
    )
    await conn.commit()
    shift = await get_shift(cursor.lastrowid)
    await create_privacy_audit_log(actor_user_id=actor, action="shift_created", details=name)
    return shift or {}


def _default_expected_minutes(start: str, end: str, break_minutes: int, overnight: bool) -> int:
    st = _parse_time(start)
    et = _parse_time(end)
    base = date.today()
    start_dt = datetime.combine(base, st)
    end_dt = datetime.combine(base, et)
    if overnight or end_dt <= start_dt:
        end_dt += timedelta(days=1)
    return max(0, int((end_dt - start_dt).total_seconds() // 60) - break_minutes)


async def get_shift(shift_id: int) -> dict[str, Any] | None:
    await enforce_hr_access()
    conn = await db.get_db()
    rows = await conn.execute_fetchall("SELECT * FROM shifts WHERE id = ?", (shift_id,))
    return _row(rows[0]) if rows else None


async def list_shifts(*, active_only: bool = False) -> list[dict[str, Any]]:
    await enforce_hr_access()
    conn = await db.get_db()
    query = "SELECT * FROM shifts"
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY name"
    rows = await conn.execute_fetchall(query)
    return [_row(r) for r in rows]


async def update_shift(shift_id: int, *, actor: str = "system", **fields: Any) -> dict[str, Any] | None:
    await enforce_hr_access()
    allowed = (
        "name",
        "start_time",
        "end_time",
        "timezone",
        "break_minutes",
        "grace_late_minutes",
        "grace_leave_early_minutes",
        "expected_work_minutes",
        "color",
        "is_overnight",
        "is_active",
    )
    sets: list[str] = []
    values: list[Any] = []
    for key in allowed:
        if key in fields and fields[key] is not None:
            value = fields[key]
            if key in {"is_overnight", "is_active"}:
                value = int(bool(value))
            sets.append(f"{key} = ?")
            values.append(value)
    if not sets:
        return await get_shift(shift_id)
    sets.append("updated_at = ?")
    values.extend([_now(), shift_id])
    conn = await db.get_db()
    await conn.execute(f"UPDATE shifts SET {', '.join(sets)} WHERE id = ?", values)
    await conn.commit()
    await create_privacy_audit_log(actor_user_id=actor, action="shift_updated", details=str(shift_id))
    return await get_shift(shift_id)


# ─── Shift assignments ────────────────────────────────────


async def create_shift_assignment(
    *,
    shift_id: int,
    employee_id: int | None = None,
    department_id: int | None = None,
    group_id: int | None = None,
    valid_from: str,
    valid_to: str | None = None,
    weekday_rules: dict[str, bool] | None = None,
    priority: int | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    await enforce_hr_access()
    if priority is None:
        if employee_id:
            priority = 100
        elif group_id:
            priority = 50
        elif department_id:
            priority = 25
        else:
            priority = 0
    rules_json = json.dumps(weekday_rules or {})
    conn = await db.get_db()
    cursor = await conn.execute(
        """INSERT INTO shift_assignments
           (employee_id, department_id, group_id, shift_id, valid_from, valid_to,
            weekday_rules, priority, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            employee_id,
            department_id,
            group_id,
            shift_id,
            valid_from,
            valid_to,
            rules_json,
            priority,
            _now(),
        ),
    )
    await conn.commit()
    assignment = await get_shift_assignment(cursor.lastrowid)
    await create_privacy_audit_log(actor_user_id=actor, action="shift_assignment_created", details=str(shift_id))
    return assignment or {}


async def get_shift_assignment(assignment_id: int) -> dict[str, Any] | None:
    conn = await db.get_db()
    rows = await conn.execute_fetchall("SELECT * FROM shift_assignments WHERE id = ?", (assignment_id,))
    return _row(rows[0]) if rows else None


def _weekday_matches(rules_json: str, day: date) -> bool:
    if not rules_json or rules_json == "{}":
        return True
    try:
        rules = json.loads(rules_json)
    except json.JSONDecodeError:
        return True
    if not rules:
        return True
    key = WEEKDAYS[day.weekday()]
    return bool(rules.get(key, rules.get(key.capitalize(), True)))


def _assignment_valid(assignment: dict[str, Any], day: date) -> bool:
    valid_from = date.fromisoformat(str(assignment["valid_from"])[:10])
    if day < valid_from:
        return False
    valid_to = assignment.get("valid_to")
    if valid_to:
        if day > date.fromisoformat(str(valid_to)[:10]):
            return False
    return _weekday_matches(str(assignment.get("weekday_rules") or ""), day)


async def resolve_shift_for_employee(employee_id: int, day: date | None = None) -> dict[str, Any] | None:
    await enforce_hr_access()
    day = day or date.today()
    conn = await db.get_db()
    emp_rows = await conn.execute_fetchall("SELECT * FROM employees WHERE id = ?", (employee_id,))
    if not emp_rows:
        return None
    employee = _row(emp_rows[0])
    assignments = await conn.execute_fetchall(
        """SELECT * FROM shift_assignments
           WHERE shift_id IN (SELECT id FROM shifts WHERE is_active = 1)
           ORDER BY priority DESC, id DESC"""
    )
    candidates: list[tuple[int, dict[str, Any]]] = []
    for row in assignments:
        assignment = _row(row)
        if not _assignment_valid(assignment, day):
            continue
        if assignment.get("employee_id") and int(assignment["employee_id"]) == employee_id:
            candidates.append((int(assignment["priority"]), assignment))
        elif employee.get("group_id") and assignment.get("group_id") and int(assignment["group_id"]) == int(employee["group_id"]):
            candidates.append((int(assignment["priority"]), assignment))
        elif employee.get("department_id") and assignment.get("department_id") and int(assignment["department_id"]) == int(employee["department_id"]):
            candidates.append((int(assignment["priority"]), assignment))
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        shift = await get_shift(int(candidates[0][1]["shift_id"]))
        if shift:
            return shift
    if employee.get("default_shift_id"):
        shift = await get_shift(int(employee["default_shift_id"]))
        if shift:
            return shift
    default_id = await db.get_app_setting("hr_default_shift_id", "0")
    if default_id and int(default_id):
        return await get_shift(int(default_id))
    return None


def shift_window(shift: dict[str, Any], day: date) -> tuple[datetime, datetime]:
    tz = ZoneInfo(str(shift.get("timezone") or "UTC"))
    st = _parse_time(str(shift["start_time"]))
    et = _parse_time(str(shift["end_time"]))
    start = datetime.combine(day, st, tzinfo=tz)
    end = datetime.combine(day, et, tzinfo=tz)
    if int(shift.get("is_overnight") or 0) or end <= start:
        end += timedelta(days=1)
    return start.astimezone(UTC), end.astimezone(UTC)
