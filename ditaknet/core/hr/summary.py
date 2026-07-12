"""Dashboard summaries for HR attendance."""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from typing import Any

from ditaknet import database as db
from ditaknet.core.employee_presence import summarize_presence
from ditaknet.core.hr.access import enforce_hr_access
from ditaknet.core.hr.attendance import list_attendance_days


async def today_attendance_summary() -> dict[str, Any]:
    await enforce_hr_access()
    today = date.today()
    rows = await list_attendance_days(day=today)
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    presence = await summarize_presence()
    return {
        "date": today.isoformat(),
        "total_employees": presence.get("total_employees", 0),
        "present_now": counts.get("present", 0) + counts.get("late", 0),
        "remote_now": presence.get("remote", 0),
        "absent": counts.get("absent", 0),
        "late": counts.get("late", 0),
        "left_early": counts.get("left_early", 0),
        "currently_onsite": presence.get("onsite", 0),
        "currently_remote": presence.get("remote", 0),
        "rows": len(rows),
    }


async def attendance_dashboard_summary() -> dict[str, Any]:
    await enforce_hr_access()
    today = await today_attendance_summary()
    now = date.today()
    start = date(now.year, now.month, 1).isoformat()
    end = date(now.year, now.month, monthrange(now.year, now.month)[1]).isoformat()
    conn = await db.get_db()
    agg = await conn.execute_fetchall(
        """SELECT
             COALESCE(SUM(worked_minutes), 0) AS worked,
             COALESCE(SUM(absence_minutes), 0) AS absence,
             COALESCE(SUM(overtime_minutes), 0) AS overtime,
             SUM(CASE WHEN confidence = 'low' THEN 1 ELSE 0 END) AS low_confidence
           FROM attendance_days
           WHERE date >= ? AND date <= ?""",
        (start, end),
    )
    row = dict(agg[0]) if agg else {}
    return {
        "today": today,
        "monthly": {
            "total_worked_minutes": int(row.get("worked") or 0),
            "absent_minutes": int(row.get("absence") or 0),
            "overtime_minutes": int(row.get("overtime") or 0),
            "low_confidence_records": int(row.get("low_confidence") or 0),
        },
    }
