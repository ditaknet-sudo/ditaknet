"""Monthly attendance reports and CSV export."""

from __future__ import annotations

import csv
import io
from calendar import monthrange
from datetime import date
from typing import Any

from ditaknet import database as db
from ditaknet.core.employee_presence import create_privacy_audit_log
from ditaknet.core.hr.access import enforce_hr_access
from ditaknet.core.hr.employees import list_employees


def _minutes_to_hours_minutes(total: int) -> tuple[int, int]:
    hours = total // 60
    minutes = total % 60
    return hours, minutes


def _avg_time(values: list[str]) -> str | None:
    parsed = []
    for value in values:
        if not value:
            continue
        try:
            from datetime import datetime

            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            parsed.append(dt.hour * 60 + dt.minute)
        except ValueError:
            continue
    if not parsed:
        return None
    avg = sum(parsed) // len(parsed)
    return f"{avg // 60:02d}:{avg % 60:02d}"


async def monthly_report(
    *,
    month: str,
    department_id: int | None = None,
    group_id: int | None = None,
    employee_id: int | None = None,
    office_id: int | None = None,
    status: str = "",
    confidence: str = "",
    department_ids: list[int] | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    await enforce_hr_access()
    year, mon = map(int, month.split("-"))
    last_day = monthrange(year, mon)[1]
    start = date(year, mon, 1).isoformat()
    end = date(year, mon, last_day).isoformat()

    employees = await list_employees(
        department_id=department_id,
        group_id=group_id,
        department_ids=department_ids,
    )
    if employee_id is not None:
        employees = [e for e in employees if int(e["id"]) == employee_id]

    conn = await db.get_db()
    rows: list[dict[str, Any]] = []
    low_confidence_count = 0
    manual_corrections = 0

    for employee in employees:
        eid = int(employee["id"])
        conditions = ["employee_id = ?", "date >= ?", "date <= ?"]
        params: list[Any] = [eid, start, end]
        if status:
            conditions.append("status = ?")
            params.append(status)
        if confidence:
            conditions.append("confidence = ?")
            params.append(confidence)
        if office_id is not None:
            conditions.append("office_id = ?")
            params.append(office_id)
        query = f"SELECT * FROM attendance_days WHERE {' AND '.join(conditions)}"
        day_rows = await conn.execute_fetchall(query, params)
        days = [dict(r) for r in day_rows]
        if not days:
            continue

        present = sum(1 for d in days if d["status"] in {"present", "late", "remote", "partial", "manual"})
        absent = sum(1 for d in days if d["status"] == "absent")
        late = sum(1 for d in days if d["status"] == "late" or int(d.get("late_minutes") or 0) > 0)
        early = sum(1 for d in days if d["status"] == "left_early" or int(d.get("early_leave_minutes") or 0) > 0)
        remote = sum(1 for d in days if d["status"] == "remote")
        expected_work = sum(int(d.get("expected_work_minutes") or 0) for d in days)
        worked = sum(int(d.get("worked_minutes") or 0) for d in days)
        overtime = sum(int(d.get("overtime_minutes") or 0) for d in days)
        absence_hours = sum(int(d.get("absence_minutes") or 0) for d in days)
        low = sum(1 for d in days if d.get("confidence") == "low")
        manual = sum(1 for d in days if int(d.get("manually_adjusted") or 0))
        low_confidence_count += low
        manual_corrections += manual

        office_breakdown: dict[str, int] = {}
        default_office = employee.get("default_office_name") or ""
        for d in days:
            raw_summary = str(d.get("worked_office_summary") or "").strip()
            if not raw_summary:
                continue
            try:
                import json

                parsed = json.loads(raw_summary)
                for oid, minutes in parsed.items():
                    office_breakdown[oid] = office_breakdown.get(oid, 0) + int(minutes)
            except json.JSONDecodeError:
                continue

        wh, wm = _minutes_to_hours_minutes(worked)
        rows.append(
            {
                "employee_id": eid,
                "employee_name": employee.get("full_name"),
                "department": employee.get("department_name") or "",
                "group": employee.get("group_name") or "",
                "default_office": default_office,
                "actual_office_ids": [int(d.get("office_id")) for d in days if d.get("office_id")],
                "office_breakdown": office_breakdown,
                "expected_work_days": len([d for d in days if int(d.get("expected_work_minutes") or 0) > 0]),
                "present_days": present,
                "absent_days": absent,
                "late_days": late,
                "early_leave_days": early,
                "remote_days": remote,
                "total_expected_minutes": expected_work,
                "total_worked_minutes": worked,
                "total_worked_hours": wh,
                "total_worked_minutes_remainder": wm,
                "worked_summary_en": f"Employee worked {wh} hours {wm} minutes this month.",
                "worked_summary_hy": f"Աշխատակիցը այս ամիս աշխատել է {wh} ժամ {wm} րոպե",
                "worked_summary_ru": f"Сотрудник отработал {wh} часов {wm} минут за этот месяц.",
                "overtime_minutes": overtime,
                "absence_minutes": absence_hours,
                "average_first_seen": _avg_time([str(d.get("first_seen_at") or "") for d in days]),
                "average_last_seen": _avg_time([str(d.get("last_seen_at") or "") for d in days]),
                "confidence_summary": {
                    "high": sum(1 for d in days if d.get("confidence") == "high"),
                    "medium": sum(1 for d in days if d.get("confidence") == "medium"),
                    "low": low,
                },
                "manual_corrections_count": manual,
                "low_confidence_warning": low > len(days) // 2,
            }
        )

    await create_privacy_audit_log(
        actor_user_id=actor,
        action="attendance_monthly_report_viewed",
        details=month,
    )
    return {
        "month": month,
        "rows": rows,
        "summary": {
            "employees": len(rows),
            "low_confidence_records": low_confidence_count,
            "manual_corrections": manual_corrections,
        },
    }


async def export_monthly_csv(
    *,
    month: str,
    department_id: int | None = None,
    group_id: int | None = None,
    employee_id: int | None = None,
    department_ids: list[int] | None = None,
    actor: str = "system",
) -> str:
    report = await monthly_report(
        month=month,
        department_id=department_id,
        group_id=group_id,
        employee_id=employee_id,
        department_ids=department_ids,
        actor=actor,
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "employee_name",
            "department",
            "group",
            "expected_work_days",
            "present_days",
            "absent_days",
            "late_days",
            "early_leave_days",
            "remote_days",
            "total_expected_hours",
            "total_worked_hours",
            "overtime_hours",
            "absence_hours",
            "average_first_seen",
            "average_last_seen",
            "low_confidence_days",
            "manual_corrections",
        ]
    )
    for row in report["rows"]:
        writer.writerow(
            [
                row["employee_name"],
                row["department"],
                row["group"],
                row["expected_work_days"],
                row["present_days"],
                row["absent_days"],
                row["late_days"],
                row["early_leave_days"],
                row["remote_days"],
                round(row["total_expected_minutes"] / 60, 2),
                round(row["total_worked_minutes"] / 60, 2),
                round(row["overtime_minutes"] / 60, 2),
                round(row["absence_minutes"] / 60, 2),
                row["average_first_seen"] or "",
                row["average_last_seen"] or "",
                row["confidence_summary"]["low"],
                row["manual_corrections_count"],
            ]
        )
    await create_privacy_audit_log(
        actor_user_id=actor,
        action="attendance_monthly_report_exported",
        details=month,
    )
    return buffer.getvalue()


async def export_daily_csv(
    *,
    day: date,
    department_ids: list[int] | None = None,
    actor: str = "system",
) -> str:
    from ditaknet.core.hr.attendance import list_attendance_days

    rows = await list_attendance_days(day=day, department_ids=department_ids)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "date",
            "employee",
            "department",
            "group",
            "shift",
            "status",
            "first_seen",
            "last_seen",
            "worked_minutes",
            "late_minutes",
            "early_leave_minutes",
            "confidence",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row.get("date"),
                row.get("full_name"),
                row.get("department_name"),
                row.get("group_name"),
                row.get("shift_name"),
                row.get("status"),
                row.get("first_seen_at"),
                row.get("last_seen_at"),
                row.get("worked_minutes"),
                row.get("late_minutes"),
                row.get("early_leave_minutes"),
                row.get("confidence"),
            ]
        )
    await create_privacy_audit_log(actor_user_id=actor, action="attendance_daily_exported", details=day.isoformat())
    return buffer.getvalue()
