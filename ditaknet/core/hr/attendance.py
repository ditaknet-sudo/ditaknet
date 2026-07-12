"""Attendance day calculation from presence events."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from ditaknet import database as db
from ditaknet.core.employee_presence import create_privacy_audit_log, refresh_presence
from ditaknet.core.hr.catalog import resolve_shift_for_employee, shift_window
from ditaknet.core.hr.access import enforce_hr_access
from ditaknet.core.hr.settings import get_attendance_settings

EVENT_TYPES = {
    "seen",
    "lost",
    "onsite_start",
    "onsite_end",
    "remote_start",
    "remote_end",
    "manual_check_in",
    "manual_check_out",
    "correction",
}
ATTENDANCE_STATUSES = {
    "present",
    "late",
    "left_early",
    "partial",
    "absent",
    "remote",
    "holiday",
    "day_off",
    "manual",
    "unknown",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row(row: Any) -> dict[str, Any]:
    return dict(row)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


async def record_presence_event(
    *,
    employee_id: int,
    event_type: str,
    event_time: str | None = None,
    source: str = "manual",
    device_id: int | None = None,
    ip: str = "",
    mac: str = "",
    hostname: str = "",
    confidence: str = "medium",
    office_id: int | None = None,
) -> dict[str, Any]:
    await enforce_hr_access()
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Invalid event_type: {event_type}")
    settings = await get_attendance_settings()
    if confidence == "low" and source in {"ping_check", "arp_scan"} and not settings.get("allow_ip_only_attendance"):
        if not mac:
            raise ValueError("IP-only attendance is disabled")
    conn = await db.get_db()
    ts = event_time or _now()
    cursor = await conn.execute(
        """INSERT INTO attendance_events
           (employee_id, device_id, event_type, event_time, source, ip, mac, hostname, confidence, office_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (employee_id, device_id, event_type, ts, source, ip, mac, hostname, confidence, office_id, _now()),
    )
    await conn.commit()
    rows = await conn.execute_fetchall("SELECT * FROM attendance_events WHERE id = ?", (cursor.lastrowid,))
    return _row(rows[0])


async def list_attendance_events(
    *,
    employee_id: int | None = None,
    day: date | None = None,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    await enforce_hr_access()
    conn = await db.get_db()
    conditions: list[str] = []
    params: list[Any] = []
    if employee_id is not None:
        conditions.append("employee_id = ?")
        params.append(employee_id)
    if day is not None:
        start = datetime.combine(day, datetime.min.time(), tzinfo=UTC).isoformat()
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=UTC).isoformat()
        conditions.append("event_time >= ? AND event_time < ?")
        params.extend([start, end])
    query = "SELECT * FROM attendance_events"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY event_time LIMIT ?"
    params.append(limit)
    rows = await conn.execute_fetchall(query, params)
    return [_row(r) for r in rows]


def _merge_windows(
    events: list[dict[str, Any]],
    gap_minutes: int,
) -> list[tuple[datetime, datetime, str]]:
    """Build presence windows from seen/remote events."""
    points: list[tuple[datetime, str, str]] = []
    for event in events:
        ts = _parse_dt(event.get("event_time"))
        if not ts:
            continue
        et = str(event.get("event_type") or "")
        if et in {"seen", "onsite_start", "remote_start", "manual_check_in"}:
            kind = "remote" if "remote" in et or event.get("source") == "agent_heartbeat" else "onsite"
            points.append((ts, "start", kind))
        elif et in {"lost", "onsite_end", "remote_end", "manual_check_out"}:
            points.append((ts, "end", "any"))
    points.sort(key=lambda item: item[0])
    windows: list[tuple[datetime, datetime, str]] = []
    open_start: datetime | None = None
    open_kind = "onsite"
    for ts, action, kind in points:
        if action == "start":
            if open_start is None:
                open_start = ts
                open_kind = kind
            continue
        if open_start and ts > open_start:
            windows.append((open_start, ts, open_kind))
            open_start = None
    if open_start and points:
        windows.append((open_start, points[-1][0], open_kind))
    if gap_minutes > 0 and len(windows) > 1:
        merged: list[tuple[datetime, datetime, str]] = [windows[0]]
        for start, end, kind in windows[1:]:
            prev_start, prev_end, prev_kind = merged[-1]
            if (start - prev_end) <= timedelta(minutes=gap_minutes):
                merged[-1] = (prev_start, max(prev_end, end), prev_kind if prev_kind == kind else "mixed")
            else:
                merged.append((start, end, kind))
        windows = merged
    return windows


def _minutes_between(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() // 60))


async def calculate_attendance_day(employee_id: int, day: date | None = None) -> dict[str, Any]:
    await enforce_hr_access()
    day = day or date.today()
    settings = await get_attendance_settings()
    shift = await resolve_shift_for_employee(employee_id, day)
    gap = int(settings.get("ignore_gap_minutes", 5))
    events = await list_attendance_events(employee_id=employee_id, day=day)
    if not events:
        events = await _bridge_presence_events(employee_id, day)

    expected_start = expected_end = None
    expected_work = 0
    late_grace = early_grace = 0
    break_minutes = 0
    if shift:
        expected_start, expected_end = shift_window(shift, day)
        expected_work = int(shift.get("expected_work_minutes") or 0)
        late_grace = int(shift.get("grace_late_minutes") or 0)
        early_grace = int(shift.get("grace_leave_early_minutes") or 0)
        break_minutes = int(shift.get("break_minutes") or 0)

    windows = _merge_windows(events, gap)
    if settings.get("require_high_confidence_for_auto_attendance"):
        events = [e for e in events if str(e.get("confidence")) == "high"]
        windows = _merge_windows(events, gap)

    first_seen = last_seen = None
    worked = 0
    remote_minutes = 0
    confidences: list[str] = []
    for event in events:
        confidences.append(str(event.get("confidence") or "low"))
    for start, end, kind in windows:
        if expected_start and end < expected_start:
            continue
        if expected_end and start > expected_end:
            continue
        clip_start = max(start, expected_start) if expected_start else start
        clip_end = min(end, expected_end) if expected_end else end
        if clip_end <= clip_start:
            continue
        minutes = _minutes_between(clip_start, clip_end)
        worked += minutes
        if kind == "remote":
            remote_minutes += minutes
        if first_seen is None or clip_start < first_seen:
            first_seen = clip_start
        if last_seen is None or clip_end > last_seen:
            last_seen = clip_end

    if not settings.get("count_remote_as_work_time"):
        worked -= remote_minutes
        worked = max(0, worked)
    worked = max(0, worked - break_minutes)

    late_minutes = early_leave = overtime = absence = 0
    status = "unknown"
    confidence = "low"
    if confidences:
        if all(c == "high" for c in confidences):
            confidence = "high"
        elif any(c == "high" for c in confidences):
            confidence = "medium"

    if shift and expected_start and expected_end:
        if first_seen is None:
            status = "absent"
            absence = expected_work
        else:
            if first_seen > expected_start + timedelta(minutes=late_grace):
                late_minutes = _minutes_between(expected_start + timedelta(minutes=late_grace), first_seen)
                status = "late"
            else:
                status = "present"
            if last_seen and last_seen < expected_end - timedelta(minutes=early_grace):
                early_leave = _minutes_between(last_seen, expected_end - timedelta(minutes=early_grace))
                status = "left_early" if status == "present" else status
            if remote_minutes > 0 and worked > 0:
                status = "remote" if remote_minutes >= worked else status
            if worked < expected_work // 2 and status not in {"absent"}:
                status = "partial"
            if worked > expected_work:
                overtime = worked - expected_work
    elif first_seen:
        status = "present"

    source_summary = ",".join(sorted({str(e.get("source") or "") for e in events if e.get("source")}))[:500]

    office_minutes: dict[int, int] = {}
    for event in events:
        oid = event.get("office_id")
        if oid is None:
            continue
        ts = _parse_dt(event.get("event_time"))
        if not ts:
            continue
        office_minutes[int(oid)] = office_minutes.get(int(oid), 0) + 1
    primary_office_id = max(office_minutes, key=office_minutes.get) if office_minutes else None
    worked_office_summary = ""
    if office_minutes:
        import json

        worked_office_summary = json.dumps(
            {str(k): v for k, v in office_minutes.items()},
            ensure_ascii=False,
        )

    conn = await db.get_db()
    await conn.execute(
        """INSERT INTO attendance_days
           (employee_id, date, shift_id, expected_start, expected_end, expected_work_minutes,
            first_seen_at, last_seen_at, worked_minutes, break_minutes, late_minutes,
            early_leave_minutes, overtime_minutes, absence_minutes, status, confidence,
            source_summary, office_id, worked_office_summary, manually_adjusted, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
           ON CONFLICT(employee_id, date) DO UPDATE SET
             shift_id = excluded.shift_id,
             expected_start = excluded.expected_start,
             expected_end = excluded.expected_end,
             expected_work_minutes = excluded.expected_work_minutes,
             first_seen_at = excluded.first_seen_at,
             last_seen_at = excluded.last_seen_at,
             worked_minutes = excluded.worked_minutes,
             break_minutes = excluded.break_minutes,
             late_minutes = excluded.late_minutes,
             early_leave_minutes = excluded.early_leave_minutes,
             overtime_minutes = excluded.overtime_minutes,
             absence_minutes = excluded.absence_minutes,
             status = excluded.status,
             confidence = excluded.confidence,
             source_summary = excluded.source_summary,
             office_id = excluded.office_id,
             worked_office_summary = excluded.worked_office_summary,
             updated_at = excluded.created_at
           WHERE attendance_days.manually_adjusted = 0""",
        (
            employee_id,
            day.isoformat(),
            shift["id"] if shift else None,
            expected_start.isoformat() if expected_start else None,
            expected_end.isoformat() if expected_end else None,
            expected_work,
            first_seen.isoformat() if first_seen else None,
            last_seen.isoformat() if last_seen else None,
            worked,
            break_minutes,
            late_minutes,
            early_leave,
            overtime,
            absence,
            status,
            confidence,
            source_summary,
            primary_office_id,
            worked_office_summary,
            _now(),
        ),
    )
    await conn.commit()
    rows = await conn.execute_fetchall(
        "SELECT * FROM attendance_days WHERE employee_id = ? AND date = ?",
        (employee_id, day.isoformat()),
    )
    return _row(rows[0])


async def _bridge_presence_events(employee_id: int, day: date) -> list[dict[str, Any]]:
    """Create attendance events from legacy employee_presence rows for the day."""
    conn = await db.get_db()
    rows = await conn.execute_fetchall(
        "SELECT * FROM employee_presence WHERE employee_id = ?", (employee_id,)
    )
    if not rows:
        return []
    presence = _row(rows[0])
    first = _parse_dt(presence.get("first_seen_at"))
    last = _parse_dt(presence.get("last_seen_at"))
    if not first or not last:
        return []
    day_start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    day_end = day_start + timedelta(days=1)
    if last < day_start or first >= day_end:
        return []
    status = str(presence.get("status") or "")
    event_type = "remote_start" if status == "remote" else "seen"
    source = str(presence.get("source") or "manual_update")
    confidence = str(presence.get("confidence") or "low")
    await record_presence_event(
        employee_id=employee_id,
        event_type=event_type,
        event_time=max(first, day_start).isoformat(),
        source=source.replace("manual_update", "manual"),
        device_id=presence.get("device_id"),
        ip=str(presence.get("current_ip") or ""),
        mac=str(presence.get("detected_mac") or ""),
        hostname=str(presence.get("detected_hostname") or ""),
        confidence=confidence,
    )
    await record_presence_event(
        employee_id=employee_id,
        event_type="remote_end" if status == "remote" else "lost",
        event_time=min(last, day_end - timedelta(seconds=1)).isoformat(),
        source=source.replace("manual_update", "manual"),
        confidence=confidence,
    )
    return await list_attendance_events(employee_id=employee_id, day=day)


async def list_attendance_days(
    *,
    day: date | None = None,
    department_id: int | None = None,
    group_id: int | None = None,
    shift_id: int | None = None,
    office_id: int | None = None,
    search: str = "",
    status: str = "",
    confidence: str = "",
    department_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    await enforce_hr_access()
    day = day or date.today()
    conn = await db.get_db()
    conditions = ["a.date = ?"]
    params: list[Any] = [day.isoformat()]
    if department_id is not None:
        conditions.append("e.department_id = ?")
        params.append(department_id)
    if group_id is not None:
        conditions.append("e.group_id = ?")
        params.append(group_id)
    if shift_id is not None:
        conditions.append("a.shift_id = ?")
        params.append(shift_id)
    if office_id is not None:
        conditions.append("a.office_id = ?")
        params.append(office_id)
    if search:
        conditions.append("(e.full_name LIKE ? OR e.employee_code LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])
    if status:
        conditions.append("a.status = ?")
        params.append(status)
    if confidence:
        conditions.append("a.confidence = ?")
        params.append(confidence)
    if department_ids is not None:
        if not department_ids:
            return []
        placeholders = ",".join("?" for _ in department_ids)
        conditions.append(f"e.department_id IN ({placeholders})")
        params.extend(department_ids)
    query = f"""
        SELECT a.*,
               e.full_name,
               e.employee_code,
               e.department_id,
               e.group_id,
               d.name AS department_name,
               g.name AS group_name,
               s.name AS shift_name
        FROM attendance_days a
        JOIN employees e ON e.id = a.employee_id
        LEFT JOIN departments d ON d.id = e.department_id
        LEFT JOIN employee_groups g ON g.id = e.group_id
        LEFT JOIN shifts s ON s.id = a.shift_id
        WHERE {' AND '.join(conditions)}
        ORDER BY e.full_name
    """
    rows = await conn.execute_fetchall(query, params)
    return [_row(r) for r in rows]


async def refresh_attendance_for_date(day: date | None = None) -> dict[str, Any]:
    await enforce_hr_access()
    settings = await get_attendance_settings()
    if not settings.get("enable_employee_attendance"):
        await db.set_app_setting("enable_employee_attendance", "1")
    day = day or date.today()
    await refresh_presence()
    conn = await db.get_db()
    rows = await conn.execute_fetchall(
        "SELECT id FROM employees WHERE COALESCE(employment_status, status) = 'active'"
    )
    updated = 0
    for row in rows:
        await calculate_attendance_day(int(row["id"]), day)
        updated += 1
    return {"date": day.isoformat(), "updated": updated}


async def manual_check_in_out(
    *,
    employee_id: int,
    action: str,
    note: str = "",
    actor: str = "system",
) -> dict[str, Any]:
    await enforce_hr_access()
    settings = await get_attendance_settings()
    if not settings.get("allow_manual_corrections"):
        raise ValueError("Manual corrections are disabled")
    event_type = "manual_check_in" if action == "in" else "manual_check_out"
    event = await record_presence_event(
        employee_id=employee_id,
        event_type=event_type,
        source="manual",
        confidence="high",
    )
    await create_privacy_audit_log(
        actor_user_id=actor,
        action=f"manual_check_{action}",
        target_employee_id=employee_id,
        details=note,
    )
    await calculate_attendance_day(employee_id, date.today())
    return event


async def manual_correction(
    *,
    employee_id: int,
    day: date,
    worked_minutes: int | None = None,
    status: str | None = None,
    first_seen_at: str | None = None,
    last_seen_at: str | None = None,
    note: str = "",
    actor: str = "system",
) -> dict[str, Any]:
    await enforce_hr_access()
    settings = await get_attendance_settings()
    if not settings.get("allow_manual_corrections"):
        raise ValueError("Manual corrections are disabled")
    if not note.strip():
        raise ValueError("Correction reason is required")
    await calculate_attendance_day(employee_id, day)
    conn = await db.get_db()
    sets = ["manually_adjusted = 1", "manual_note = ?", "updated_at = ?"]
    values: list[Any] = [note.strip(), _now()]
    if worked_minutes is not None:
        sets.append("worked_minutes = ?")
        values.append(worked_minutes)
    if status and status in ATTENDANCE_STATUSES:
        sets.append("status = ?")
        values.append(status)
    if first_seen_at:
        sets.append("first_seen_at = ?")
        values.append(first_seen_at)
    if last_seen_at:
        sets.append("last_seen_at = ?")
        values.append(last_seen_at)
    values.extend([employee_id, day.isoformat()])
    await conn.execute(
        f"UPDATE attendance_days SET {', '.join(sets)} WHERE employee_id = ? AND date = ?",
        values,
    )
    await conn.commit()
    await record_presence_event(
        employee_id=employee_id,
        event_type="correction",
        source="manual",
        confidence="high",
    )
    await create_privacy_audit_log(
        actor_user_id=actor,
        action="attendance_manual_correction",
        target_employee_id=employee_id,
        details=note,
    )
    rows = await conn.execute_fetchall(
        "SELECT * FROM attendance_days WHERE employee_id = ? AND date = ?",
        (employee_id, day.isoformat()),
    )
    return _row(rows[0])
