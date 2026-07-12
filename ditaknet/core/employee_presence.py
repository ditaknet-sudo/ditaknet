"""Privacy-aware employee presence monitoring.

The feature uses approved corporate devices and workplace network signals. It
does not inspect traffic, track GPS/location, or collect personal browsing data.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from ditaknet import database as db
from ditaknet.core.licensing import license_service

STATUS_VALUES = {"onsite", "remote", "away", "offline", "unknown"}
CONNECTION_TYPES = {
    "onsite_wifi",
    "onsite_lan",
    "remote_agent",
    "vpn",
    "manual",
    "unknown",
}
CONFIDENCE_VALUES = {"high", "medium", "low"}
SOURCE_VALUES = {
    "arp_scan",
    "ping_check",
    "dhcp_lease",
    "agent_heartbeat",
    "vpn_heartbeat",
    "manual_update",
}

DEFAULT_PRIVACY_NOTICE = (
    "This feature detects presence based on approved company devices connected "
    "to the organization network or approved remote heartbeat. It does not "
    "track personal location or inspect internet traffic."
)


@dataclass(frozen=True)
class PresenceDecision:
    status: str
    connection_type: str
    confidence: str
    current_ip: str = ""
    detected_mac: str = ""
    detected_hostname: str = ""
    source: str = "manual_update"
    device_id: int | None = None
    notes: str = ""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _to_dict(row: Any) -> dict[str, Any]:
    return dict(row)


def _bool(value: Any) -> bool:
    return str(value).lower() in {"1", "true", "yes", "on"}


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


def normalize_mac(value: Any) -> str:
    """Return a comparable lowercase MAC address without separators."""
    cleaned = re.sub(r"[^0-9a-fA-F]", "", str(value or ""))
    return cleaned.lower()


def _validate_choice(name: str, value: str, allowed: set[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in allowed:
        raise ValueError(f"Invalid {name}: {value}")
    return normalized


async def enforce_employee_presence_access() -> None:
    await license_service.enforce_employee_presence_access()


async def get_presence_settings() -> dict[str, Any]:
    status = await license_service.status()
    raw_sources = await db.get_app_setting(
        "employee_presence_allowed_sources",
        '["arp_scan","ping_check","dhcp_lease","agent_heartbeat","vpn_heartbeat","manual_update"]',
    )
    try:
        sources = json.loads(raw_sources or "[]")
    except json.JSONDecodeError:
        sources = []
    return {
        "licensed": bool(status.get("employee_presence_enabled")),
        "configured_enabled": _bool(
            await db.get_app_setting("employee_presence_configured_enabled", "0")
        ),
        "presence_online_grace_minutes": int(
            await db.get_app_setting("presence_online_grace_minutes", "5") or 5
        ),
        "presence_away_after_minutes": int(
            await db.get_app_setting("presence_away_after_minutes", "15") or 15
        ),
        "presence_offline_after_minutes": int(
            await db.get_app_setting("presence_offline_after_minutes", "60") or 60
        ),
        "allowed_detection_sources": [s for s in sources if s in SOURCE_VALUES],
        "privacy_notice_text": await db.get_app_setting(
            "employee_presence_privacy_notice", DEFAULT_PRIVACY_NOTICE
        ),
    }


async def update_presence_settings(
    *,
    configured_enabled: bool,
    presence_online_grace_minutes: int = 5,
    presence_away_after_minutes: int = 15,
    presence_offline_after_minutes: int = 60,
    allowed_detection_sources: list[str] | None = None,
    privacy_notice_text: str = DEFAULT_PRIVACY_NOTICE,
    actor: str = "system",
) -> dict[str, Any]:
    await enforce_employee_presence_access()
    sources = [
        _validate_choice("source", source, SOURCE_VALUES)
        for source in (
            allowed_detection_sources
            or [
                "arp_scan",
                "ping_check",
                "dhcp_lease",
                "agent_heartbeat",
                "vpn_heartbeat",
                "manual_update",
            ]
        )
    ]
    await db.set_app_setting(
        "employee_presence_configured_enabled", "1" if configured_enabled else "0"
    )
    await db.set_app_setting(
        "presence_online_grace_minutes", str(max(1, presence_online_grace_minutes))
    )
    await db.set_app_setting(
        "presence_away_after_minutes", str(max(1, presence_away_after_minutes))
    )
    await db.set_app_setting(
        "presence_offline_after_minutes", str(max(1, presence_offline_after_minutes))
    )
    await db.set_app_setting("employee_presence_allowed_sources", json.dumps(sources))
    await db.set_app_setting(
        "employee_presence_privacy_notice", privacy_notice_text.strip() or DEFAULT_PRIVACY_NOTICE
    )
    await create_privacy_audit_log(
        actor_user_id=actor,
        action="presence_settings_changed",
        details=json.dumps({"configured_enabled": configured_enabled, "sources": sources}),
    )
    return await get_presence_settings()


async def create_privacy_audit_log(
    *,
    actor_user_id: str,
    action: str,
    target_employee_id: int | None = None,
    details: str = "",
) -> dict[str, Any]:
    conn = await db.get_db()
    cursor = await conn.execute(
        """INSERT INTO privacy_audit_logs
           (actor_user_id, action, target_employee_id, details, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (actor_user_id or "system", action, target_employee_id, details, _now()),
    )
    await conn.commit()
    rows = await conn.execute_fetchall(
        "SELECT * FROM privacy_audit_logs WHERE id = ?", (cursor.lastrowid,)
    )
    return _to_dict(rows[0])


async def create_employee(
    *,
    full_name: str,
    department: str = "",
    position: str = "",
    email: str = "",
    phone: str = "",
    employee_code: str = "",
    status: str = "active",
    privacy_notice_accepted: bool = False,
    actor: str = "system",
) -> dict[str, Any]:
    await enforce_employee_presence_access()
    status = "inactive" if status == "inactive" else "active"
    conn = await db.get_db()
    cursor = await conn.execute(
        """INSERT INTO employees
           (full_name, department, position, email, phone, employee_code,
            status, privacy_notice_accepted, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            full_name.strip(),
            department.strip(),
            position.strip(),
            email.strip(),
            phone.strip(),
            employee_code.strip(),
            status,
            int(privacy_notice_accepted),
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


async def list_employees(
    *,
    search: str = "",
    department: str = "",
    status: str = "",
) -> list[dict[str, Any]]:
    await enforce_employee_presence_access()
    conn = await db.get_db()
    conditions: list[str] = []
    params: list[Any] = []
    if search:
        conditions.append("(full_name LIKE ? OR email LIKE ? OR employee_code LIKE ?)")
        value = f"%{search}%"
        params.extend([value, value, value])
    if department:
        conditions.append("department = ?")
        params.append(department)
    if status:
        conditions.append("status = ?")
        params.append(status)
    query = "SELECT * FROM employees"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY status, full_name"
    rows = await conn.execute_fetchall(query, params)
    return [_to_dict(row) for row in rows]


async def get_employee(employee_id: int) -> dict[str, Any] | None:
    await enforce_employee_presence_access()
    conn = await db.get_db()
    rows = await conn.execute_fetchall("SELECT * FROM employees WHERE id = ?", (employee_id,))
    return _to_dict(rows[0]) if rows else None


async def update_employee(
    employee_id: int, *, actor: str = "system", **fields: Any
) -> dict[str, Any] | None:
    await enforce_employee_presence_access()
    allowed = (
        "full_name",
        "department",
        "position",
        "email",
        "phone",
        "employee_code",
        "status",
        "privacy_notice_accepted",
    )
    sets: list[str] = []
    values: list[Any] = []
    for key in allowed:
        if key in fields and fields[key] is not None:
            value = fields[key]
            if key == "privacy_notice_accepted":
                value = int(bool(value))
            if key == "status":
                value = "inactive" if value == "inactive" else "active"
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


async def deactivate_employee(employee_id: int, *, actor: str = "system") -> bool:
    updated = await update_employee(employee_id, actor=actor, status="inactive")
    return updated is not None


async def create_employee_device(
    *,
    employee_id: int,
    device_name: str,
    device_type: str = "laptop",
    mac_address: str = "",
    hostname: str = "",
    static_ip: str = "",
    last_ip: str = "",
    agent_id: str = "",
    is_primary: bool = False,
    is_approved: bool = True,
    actor: str = "system",
) -> dict[str, Any]:
    await enforce_employee_presence_access()
    if not await get_employee(employee_id):
        raise ValueError("Employee not found")
    conn = await db.get_db()
    cursor = await conn.execute(
        """INSERT INTO employee_devices
           (employee_id, device_name, device_type, mac_address, hostname,
            static_ip, last_ip, agent_id, is_primary, is_approved, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            employee_id,
            device_name.strip(),
            device_type.strip() or "laptop",
            mac_address.strip(),
            hostname.strip(),
            static_ip.strip(),
            last_ip.strip(),
            agent_id.strip(),
            int(is_primary),
            int(is_approved),
            _now(),
        ),
    )
    await conn.commit()
    device = await get_employee_device(cursor.lastrowid)
    await create_privacy_audit_log(
        actor_user_id=actor,
        action="device_linked",
        target_employee_id=employee_id,
        details=device_name.strip(),
    )
    return device or {}


async def list_employee_devices(employee_id: int | None = None) -> list[dict[str, Any]]:
    await enforce_employee_presence_access()
    conn = await db.get_db()
    if employee_id is None:
        rows = await conn.execute_fetchall(
            "SELECT * FROM employee_devices ORDER BY employee_id, id"
        )
    else:
        rows = await conn.execute_fetchall(
            "SELECT * FROM employee_devices WHERE employee_id = ? ORDER BY is_primary DESC, id",
            (employee_id,),
        )
    return [_to_dict(row) for row in rows]


async def get_employee_device(device_id: int) -> dict[str, Any] | None:
    await enforce_employee_presence_access()
    conn = await db.get_db()
    rows = await conn.execute_fetchall("SELECT * FROM employee_devices WHERE id = ?", (device_id,))
    return _to_dict(rows[0]) if rows else None


async def update_employee_device(
    device_id: int, *, actor: str = "system", **fields: Any
) -> dict[str, Any] | None:
    await enforce_employee_presence_access()
    allowed = (
        "device_name",
        "device_type",
        "mac_address",
        "hostname",
        "static_ip",
        "last_ip",
        "agent_id",
        "is_primary",
        "is_approved",
    )
    sets: list[str] = []
    values: list[Any] = []
    for key in allowed:
        if key in fields and fields[key] is not None:
            value = fields[key]
            if key in {"is_primary", "is_approved"}:
                value = int(bool(value))
            sets.append(f"{key} = ?")
            values.append(value)
    if not sets:
        return await get_employee_device(device_id)
    sets.append("updated_at = ?")
    values.extend([_now(), device_id])
    conn = await db.get_db()
    await conn.execute(f"UPDATE employee_devices SET {', '.join(sets)} WHERE id = ?", values)
    await conn.commit()
    device = await get_employee_device(device_id)
    await create_privacy_audit_log(
        actor_user_id=actor,
        action="device_updated",
        target_employee_id=device.get("employee_id") if device else None,
        details=str(device_id),
    )
    return device


async def delete_employee_device(device_id: int, *, actor: str = "system") -> bool:
    await enforce_employee_presence_access()
    device = await get_employee_device(device_id)
    if not device:
        return False
    conn = await db.get_db()
    cursor = await conn.execute("DELETE FROM employee_devices WHERE id = ?", (device_id,))
    await conn.commit()
    await create_privacy_audit_log(
        actor_user_id=actor,
        action="device_removed",
        target_employee_id=device.get("employee_id"),
        details=str(device_id),
    )
    return cursor.rowcount > 0


async def link_discovered_device_to_employee(
    *,
    employee_id: int,
    discovered_device_id: int,
    device_type: str = "laptop",
    is_primary: bool = False,
    actor: str = "system",
) -> dict[str, Any]:
    await enforce_employee_presence_access()
    discovered = await db.get_discovered_device(discovered_device_id)
    if not discovered:
        raise ValueError("Discovered device not found")
    name = (
        discovered.get("hostname")
        or discovered.get("vendor")
        or f"device-{discovered.get('ip_address')}"
    )
    return await create_employee_device(
        employee_id=employee_id,
        device_name=str(name)[:255],
        device_type=device_type,
        mac_address=str(discovered.get("mac_address") or ""),
        hostname=str(discovered.get("hostname") or ""),
        static_ip=str(discovered.get("ip_address") or ""),
        last_ip=str(discovered.get("ip_address") or ""),
        is_primary=is_primary,
        is_approved=True,
        actor=actor,
    )


async def get_employee_presence(employee_id: int) -> dict[str, Any] | None:
    await enforce_employee_presence_access()
    conn = await db.get_db()
    rows = await conn.execute_fetchall(
        "SELECT * FROM employee_presence WHERE employee_id = ?", (employee_id,)
    )
    return _to_dict(rows[0]) if rows else None


async def upsert_employee_presence(
    *,
    employee_id: int,
    status: str,
    connection_type: str = "unknown",
    confidence: str = "low",
    device_id: int | None = None,
    current_ip: str = "",
    detected_mac: str = "",
    detected_hostname: str = "",
    source: str = "manual_update",
    notes: str = "",
) -> dict[str, Any]:
    await enforce_employee_presence_access()
    status = _validate_choice("status", status, STATUS_VALUES)
    connection_type = _validate_choice("connection_type", connection_type, CONNECTION_TYPES)
    confidence = _validate_choice("confidence", confidence, CONFIDENCE_VALUES)
    source = _validate_choice("source", source, SOURCE_VALUES)
    existing = await get_employee_presence(employee_id)
    old_status = str(existing.get("status") or "") if existing else ""
    now = _now()
    first_seen_at = existing.get("first_seen_at") if existing else now
    last_status_change_at = (
        existing.get("last_status_change_at") if existing and old_status == status else now
    )
    conn = await db.get_db()
    await conn.execute(
        """INSERT INTO employee_presence
           (employee_id, device_id, status, connection_type, confidence,
            current_ip, detected_mac, detected_hostname, source, first_seen_at,
            last_seen_at, last_status_change_at, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(employee_id) DO UPDATE SET
             device_id = excluded.device_id,
             status = excluded.status,
             connection_type = excluded.connection_type,
             confidence = excluded.confidence,
             current_ip = excluded.current_ip,
             detected_mac = excluded.detected_mac,
             detected_hostname = excluded.detected_hostname,
             source = excluded.source,
             last_seen_at = excluded.last_seen_at,
             last_status_change_at = excluded.last_status_change_at,
             notes = excluded.notes""",
        (
            employee_id,
            device_id,
            status,
            connection_type,
            confidence,
            current_ip,
            detected_mac,
            detected_hostname,
            source,
            first_seen_at,
            now,
            last_status_change_at,
            notes,
        ),
    )
    if old_status != status:
        await conn.execute(
            """INSERT INTO employee_presence_events
               (employee_id, device_id, old_status, new_status, source, ip,
                confidence, event_time)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (employee_id, device_id, old_status, status, source, current_ip, confidence, now),
        )
    await conn.commit()
    return await get_employee_presence(employee_id) or {}


async def list_employee_presence() -> list[dict[str, Any]]:
    await enforce_employee_presence_access()
    conn = await db.get_db()
    rows = await conn.execute_fetchall(
        """
        SELECT
          e.id AS employee_id,
          e.full_name,
          e.department,
          e.position,
          e.status AS employee_status,
          e.privacy_notice_accepted,
          p.id,
          p.device_id,
          COALESCE(p.status, 'unknown') AS status,
          COALESCE(p.connection_type, 'unknown') AS connection_type,
          COALESCE(p.confidence, 'low') AS confidence,
          COALESCE(p.current_ip, '') AS current_ip,
          COALESCE(p.detected_mac, '') AS detected_mac,
          COALESCE(p.detected_hostname, '') AS detected_hostname,
          COALESCE(p.source, '') AS source,
          p.first_seen_at,
          p.last_seen_at,
          p.last_status_change_at,
          COALESCE(p.notes, '') AS notes,
          p.office_id,
          o.name AS office_name,
          o.code AS office_code,
          d.device_name
        FROM employees e
        LEFT JOIN employee_presence p ON p.employee_id = e.id
        LEFT JOIN offices o ON o.id = p.office_id
        LEFT JOIN employee_devices d ON d.id = p.device_id
        ORDER BY e.department, e.full_name
        """
    )
    return [_to_dict(row) for row in rows]


async def list_presence_events(
    employee_id: int | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    await enforce_employee_presence_access()
    conn = await db.get_db()
    if employee_id is None:
        rows = await conn.execute_fetchall(
            "SELECT * FROM employee_presence_events ORDER BY event_time DESC LIMIT ?",
            (limit,),
        )
    else:
        rows = await conn.execute_fetchall(
            """SELECT * FROM employee_presence_events
               WHERE employee_id = ?
               ORDER BY event_time DESC LIMIT ?""",
            (employee_id, limit),
        )
    return [_to_dict(row) for row in rows]


async def summarize_presence() -> dict[str, Any]:
    await enforce_employee_presence_access()
    rows = await list_employee_presence()
    counts = {status: 0 for status in STATUS_VALUES}
    active = 0
    last_refresh = ""
    for row in rows:
        if row.get("employee_status") == "active":
            active += 1
        status = str(row.get("status") or "unknown")
        counts[status if status in counts else "unknown"] += 1
        last_seen = row.get("last_seen_at") or ""
        if last_seen and last_seen > last_refresh:
            last_refresh = last_seen
    return {
        "total_employees": len(rows),
        "active_employees_now": counts["onsite"] + counts["remote"],
        "onsite": counts["onsite"],
        "remote": counts["remote"],
        "away": counts["away"],
        "offline": counts["offline"],
        "unknown": counts["unknown"],
        "active_employees": active,
        "last_refresh": last_refresh,
        "settings": await get_presence_settings(),
    }


def decide_presence_for_device(
    device: dict[str, Any],
    network_signals: list[dict[str, Any]],
    agents_by_id: dict[str, dict[str, Any]],
    current_presence: dict[str, Any] | None,
    settings: dict[str, Any],
    now: datetime,
) -> PresenceDecision:
    device_id = int(device["id"])
    device_mac = normalize_mac(device.get("mac_address"))
    device_hostname = str(device.get("hostname") or "").strip().lower()
    known_ips = {
        str(device.get("static_ip") or "").strip(),
        str(device.get("last_ip") or "").strip(),
    }
    known_ips.discard("")

    for signal in network_signals:
        signal_mac = normalize_mac(signal.get("mac_address"))
        if device_mac and signal_mac and device_mac == signal_mac:
            return PresenceDecision(
                status="onsite",
                connection_type="onsite_lan",
                confidence="high",
                current_ip=str(signal.get("ip_address") or ""),
                detected_mac=str(signal.get("mac_address") or ""),
                detected_hostname=str(signal.get("hostname") or ""),
                source=str(signal.get("discovery_source") or "arp_scan")
                if str(signal.get("discovery_source") or "arp_scan") in SOURCE_VALUES
                else "arp_scan",
                device_id=device_id,
            )

    for signal in network_signals:
        signal_host = str(signal.get("hostname") or "").strip().lower()
        signal_ip = str(signal.get("ip_address") or "").strip()
        if device_hostname and signal_host == device_hostname and signal_ip in known_ips:
            return PresenceDecision(
                status="onsite",
                connection_type="onsite_lan",
                confidence="medium",
                current_ip=signal_ip,
                detected_mac=str(signal.get("mac_address") or ""),
                detected_hostname=str(signal.get("hostname") or ""),
                source="arp_scan",
                device_id=device_id,
                notes="Hostname and IP match. MAC registration improves accuracy.",
            )

    for signal in network_signals:
        signal_ip = str(signal.get("ip_address") or "").strip()
        if signal_ip and signal_ip in known_ips:
            return PresenceDecision(
                status="onsite",
                connection_type="onsite_lan",
                confidence="low",
                current_ip=signal_ip,
                detected_mac=str(signal.get("mac_address") or ""),
                detected_hostname=str(signal.get("hostname") or ""),
                source="ping_check",
                device_id=device_id,
                notes=(
                    "IP-only matching can be inaccurate. Use static DHCP reservation "
                    "or register MAC address."
                ),
            )

    agent_id = str(device.get("agent_id") or "").strip()
    agent = agents_by_id.get(agent_id)
    if agent:
        heartbeat = _parse_dt(agent.get("last_heartbeat_at"))
        grace = int(settings.get("presence_online_grace_minutes", 5))
        if heartbeat and now - heartbeat <= timedelta(minutes=grace):
            return PresenceDecision(
                status="remote",
                connection_type="remote_agent",
                confidence="high",
                source="agent_heartbeat",
                device_id=device_id,
            )

    last_seen = _parse_dt((current_presence or {}).get("last_seen_at"))
    previous_status = str((current_presence or {}).get("status") or "unknown")
    if last_seen:
        age = now - last_seen
        if age >= timedelta(minutes=int(settings.get("presence_offline_after_minutes", 60))):
            return PresenceDecision(
                status="offline",
                connection_type="unknown",
                confidence="low",
                source="manual_update",
                device_id=device_id,
            )
        if age >= timedelta(minutes=int(settings.get("presence_away_after_minutes", 15))):
            return PresenceDecision(
                status="away",
                connection_type="unknown",
                confidence="low",
                source="manual_update",
                device_id=device_id,
            )
        return PresenceDecision(
            status=previous_status if previous_status in STATUS_VALUES else "unknown",
            connection_type=str((current_presence or {}).get("connection_type") or "unknown"),
            confidence=str((current_presence or {}).get("confidence") or "low"),
            source=str((current_presence or {}).get("source") or "manual_update"),
            device_id=device_id,
        )

    return PresenceDecision(
        status="unknown",
        connection_type="unknown",
        confidence="low",
        source="manual_update",
        device_id=device_id,
    )


async def _list_agents() -> list[dict[str, Any]]:
    conn = await db.get_db()
    rows = await conn.execute_fetchall("SELECT * FROM agents WHERE enabled = 1")
    return [_to_dict(row) for row in rows]


def _priority(decision: PresenceDecision) -> tuple[int, int]:
    status_score = {"onsite": 4, "remote": 3, "away": 2, "offline": 1, "unknown": 0}
    confidence_score = {"high": 3, "medium": 2, "low": 1}
    return (status_score.get(decision.status, 0), confidence_score.get(decision.confidence, 0))


async def refresh_presence() -> dict[str, Any]:
    await enforce_employee_presence_access()
    settings = await get_presence_settings()
    employees = [employee for employee in await list_employees(status="active")]
    devices = await list_employee_devices()
    devices_by_employee: dict[int, list[dict[str, Any]]] = {}
    for device in devices:
        if int(device.get("is_approved") or 0):
            devices_by_employee.setdefault(int(device["employee_id"]), []).append(device)
    network_signals = await db.list_discovered_devices(limit=1000)
    agents = {str(agent["id"]): agent for agent in await _list_agents()}
    now = datetime.now(UTC)
    updated = 0

    for employee in employees:
        current = await get_employee_presence(int(employee["id"]))
        decisions = [
            decide_presence_for_device(device, network_signals, agents, current, settings, now)
            for device in devices_by_employee.get(int(employee["id"]), [])
        ]
        decision = (
            max(decisions, key=_priority)
            if decisions
            else PresenceDecision(status="unknown", connection_type="unknown", confidence="low")
        )
        presence = await upsert_employee_presence(
            employee_id=int(employee["id"]),
            device_id=decision.device_id,
            status=decision.status,
            connection_type=decision.connection_type,
            confidence=decision.confidence,
            current_ip=decision.current_ip,
            detected_mac=decision.detected_mac,
            detected_hostname=decision.detected_hostname,
            source=decision.source,
            notes=decision.notes,
        )
        if decision.current_ip and decision.device_id:
            conn = await db.get_db()
            await conn.execute(
                "UPDATE employee_devices SET last_ip = ?, updated_at = ? WHERE id = ?",
                (decision.current_ip, _now(), decision.device_id),
            )
            await conn.commit()
        if presence:
            updated += 1
    summary = await summarize_presence()
    summary["updated"] = updated
    return summary


async def manual_status_update(
    *,
    employee_id: int,
    status: str,
    connection_type: str = "manual",
    confidence: str = "medium",
    notes: str = "",
    actor: str = "system",
) -> dict[str, Any]:
    await enforce_employee_presence_access()
    if not await get_employee(employee_id):
        raise ValueError("Employee not found")
    presence = await upsert_employee_presence(
        employee_id=employee_id,
        status=status,
        connection_type=connection_type,
        confidence=confidence,
        source="manual_update",
        notes=notes,
    )
    await create_privacy_audit_log(
        actor_user_id=actor,
        action="manual_status_changed",
        target_employee_id=employee_id,
        details=json.dumps({"status": status, "notes": notes}),
    )
    return presence


async def report_daily() -> dict[str, Any]:
    await enforce_employee_presence_access()
    today = datetime.now(UTC).date().isoformat()
    events = await list_presence_events(limit=1000)
    today_events = [event for event in events if str(event.get("event_time", "")).startswith(today)]
    counts = {status: 0 for status in STATUS_VALUES}
    for event in today_events:
        status = str(event.get("new_status") or "unknown")
        counts[status if status in counts else "unknown"] += 1
    return {"date": today, "events": len(today_events), "status_changes": counts}


async def report_range(start: str = "", end: str = "") -> dict[str, Any]:
    await enforce_employee_presence_access()
    events = await list_presence_events(limit=10000)
    filtered = [
        event
        for event in events
        if (not start or str(event.get("event_time", "")) >= start)
        and (not end or str(event.get("event_time", "")) <= end)
    ]
    return {"start": start, "end": end, "events": filtered, "count": len(filtered)}
