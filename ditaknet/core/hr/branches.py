"""Branch agent authentication, heartbeat, and presence ingestion."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from ditaknet import database as db
from ditaknet.core.employee_presence import (
    CONNECTION_TYPES,
    CONFIDENCE_VALUES,
    SOURCE_VALUES,
    STATUS_VALUES,
    create_privacy_audit_log,
    normalize_mac,
)
from ditaknet.core.hr.offices import hash_branch_token
from ditaknet.core.licensing import license_service


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _to_dict(row: Any) -> dict[str, Any]:
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


async def authenticate_branch_token(raw_token: str) -> dict[str, Any]:
    await license_service.enforce_multi_office_access()
    limits = await license_service.get_limits()
    if not limits.branch_agent_enabled:
        raise PermissionError("Branch agents are not enabled for this license")
    token = str(raw_token or "").strip()
    if not token:
        raise PermissionError("Branch token required")
    token_hash = hash_branch_token(token)
    conn = await db.get_db()
    rows = await conn.execute_fetchall(
        "SELECT * FROM offices WHERE branch_token_hash = ? AND status = 'active'",
        (token_hash,),
    )
    if not rows:
        raise PermissionError("Invalid or disabled branch token")
    return _to_dict(rows[0])


async def register_branch(
    *,
    name: str,
    code: str,
    timezone: str = "UTC",
    subnet_cidr: str = "",
    address: str = "",
    city: str = "",
    actor: str = "system",
) -> dict[str, Any]:
    """Admin-only: create office and return one-time branch token."""
    from ditaknet.core.hr.offices import create_office

    await license_service.enforce_multi_office_access()
    office = await create_office(
        name=name,
        code=code,
        timezone=timezone,
        subnet_cidr=subnet_cidr,
        address=address,
        city=city,
        actor=actor,
        issue_token=True,
    )
    await create_privacy_audit_log(
        actor_user_id=actor,
        action="branch_registered",
        details=f"office_id={office.get('id')} code={code}",
    )
    return office


async def record_branch_heartbeat(
    office: dict[str, Any],
    *,
    agent_version: str = "",
    hostname: str = "",
    local_subnet: str = "",
    scan_status: str = "idle",
) -> dict[str, Any]:
    await license_service.enforce_multi_office_access()
    office_id = int(office["id"])
    now = _now()
    conn = await db.get_db()
    await conn.execute(
        "UPDATE offices SET last_agent_seen_at = ?, updated_at = ? WHERE id = ?",
        (now, now, office_id),
    )
    await conn.execute(
        """INSERT INTO branch_agents
           (office_id, hostname, agent_version, local_subnet, scan_status,
            last_heartbeat_at, is_active, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
           ON CONFLICT(office_id, hostname) DO UPDATE SET
             agent_version = excluded.agent_version,
             local_subnet = excluded.local_subnet,
             scan_status = excluded.scan_status,
             last_heartbeat_at = excluded.last_heartbeat_at,
             is_active = 1,
             updated_at = excluded.updated_at""",
        (
            office_id,
            hostname.strip() or "unknown",
            agent_version.strip() or "unknown",
            local_subnet.strip(),
            scan_status.strip() or "idle",
            now,
            now,
            now,
        ),
    )
    await conn.commit()
    rows = await conn.execute_fetchall(
        "SELECT * FROM branch_agents WHERE office_id = ? AND hostname = ?",
        (office_id, hostname.strip() or "unknown"),
    )
    agent = _to_dict(rows[0]) if rows else {}
    await create_privacy_audit_log(
        actor_user_id=f"branch:{office.get('code')}",
        action="branch_heartbeat",
        details=f"office_id={office_id} hostname={hostname}",
    )
    return agent


async def _match_employee_device(
    *,
    mac_address: str = "",
    ip_address: str = "",
    hostname: str = "",
) -> tuple[int | None, int | None, str]:
    """Return (employee_id, device_id, confidence)."""
    conn = await db.get_db()
    mac = normalize_mac(mac_address)
    if mac:
        rows = await conn.execute_fetchall(
            """SELECT id, employee_id FROM employee_devices
               WHERE is_approved = 1 AND REPLACE(REPLACE(LOWER(mac_address), ':', ''), '-', '') = ?""",
            (mac,),
        )
        if rows:
            return int(rows[0]["employee_id"]), int(rows[0]["id"]), "high"
    host = str(hostname or "").strip().lower()
    if host:
        rows = await conn.execute_fetchall(
            """SELECT id, employee_id FROM employee_devices
               WHERE is_approved = 1 AND LOWER(hostname) = ?""",
            (host,),
        )
        if rows:
            return int(rows[0]["employee_id"]), int(rows[0]["id"]), "medium"
    ip = str(ip_address or "").strip()
    if ip:
        rows = await conn.execute_fetchall(
            """SELECT id, employee_id FROM employee_devices
               WHERE is_approved = 1 AND ip_address = ?""",
            (ip,),
        )
        if rows:
            return int(rows[0]["employee_id"]), int(rows[0]["id"]), "low"
    return None, None, "low"


def _confidence_rank(value: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(str(value).lower(), 0)


async def ingest_presence_event(
    office: dict[str, Any],
    *,
    branch_agent_id: int | None,
    office_code: str,
    detected_at: str,
    mac_address: str = "",
    hostname: str = "",
    ip_address: str = "",
    source: str = "branch_agent",
    confidence: str = "low",
    device_fingerprint: str = "",
    agent_version: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    await license_service.enforce_multi_office_access()
    office_id = int(office["id"])
    if office_code.strip().lower() != str(office.get("code") or "").lower():
        raise ValueError("office_code does not match authenticated branch")

    employee_id, device_id, match_confidence = await _match_employee_device(
        mac_address=mac_address,
        ip_address=ip_address,
        hostname=hostname,
    )
    event_confidence = confidence if confidence in CONFIDENCE_VALUES else match_confidence
    if _confidence_rank(match_confidence) > _confidence_rank(event_confidence):
        event_confidence = match_confidence

    conn = await db.get_db()
    await conn.execute(
        """INSERT INTO branch_presence_events
           (office_id, branch_agent_id, employee_id, device_id, detected_at,
            mac_address, hostname, ip_address, source, confidence, payload_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            office_id,
            branch_agent_id,
            employee_id,
            device_id,
            detected_at or _now(),
            mac_address,
            hostname,
            ip_address,
            source,
            event_confidence,
            json.dumps(payload or {}),
        ),
    )
    await conn.commit()

    result: dict[str, Any] = {
        "matched": employee_id is not None,
        "employee_id": employee_id,
        "device_id": device_id,
        "confidence": event_confidence,
        "office_id": office_id,
    }

    if employee_id is None:
        await create_privacy_audit_log(
            actor_user_id=f"branch:{office.get('code')}",
            action="branch_presence_unmatched",
            details=f"ip={ip_address} mac={mac_address[:8]}...",
        )
        return result

    current_rows = await conn.execute_fetchall(
        "SELECT * FROM employee_presence WHERE employee_id = ?", (employee_id,)
    )
    current = _to_dict(current_rows[0]) if current_rows else None
    prev_office_id = int(current["office_id"]) if current and current.get("office_id") else None

    connection_type = "onsite_lan"
    if source in CONNECTION_TYPES:
        connection_type = source
    elif "wifi" in str(source).lower():
        connection_type = "onsite_wifi"

    conflict = False
    if prev_office_id and prev_office_id != office_id:
        prev_seen = _parse_dt(current.get("last_seen_at") if current else None)
        if prev_seen and datetime.now(UTC) - prev_seen <= timedelta(minutes=10):
            prev_conf = str((current or {}).get("confidence") or "low")
            if _confidence_rank(prev_conf) > _confidence_rank(event_confidence):
                conflict = True
                await create_privacy_audit_log(
                    actor_user_id=f"branch:{office.get('code')}",
                    action="presence_conflict",
                    details=json.dumps(
                        {
                            "employee_id": employee_id,
                            "office_a": prev_office_id,
                            "office_b": office_id,
                            "kept_office": prev_office_id,
                        }
                    ),
                )
                result["conflict"] = True
                result["kept_office_id"] = prev_office_id
                return result

    if not conflict and prev_office_id and prev_office_id != office_id:
        await create_privacy_audit_log(
            actor_user_id=f"branch:{office.get('code')}",
            action="employee_office_moved",
            details=json.dumps(
                {"employee_id": employee_id, "from_office": prev_office_id, "to_office": office_id}
            ),
        )

    if "branch_agent" not in SOURCE_VALUES:
        pass
    presence_source = "arp_scan" if "arp" in source.lower() else "ping_check"
    if source == "branch_agent" or source.startswith("branch"):
        presence_source = "arp_scan" if mac_address else "ping_check"

    await upsert_employee_presence_with_office(
        employee_id=employee_id,
        status="onsite",
        connection_type=connection_type,
        confidence=event_confidence,
        device_id=device_id,
        current_ip=ip_address,
        detected_mac=mac_address,
        detected_hostname=hostname,
        source=presence_source,
        office_id=office_id,
        branch_agent_id=branch_agent_id,
        notes=f"Detected by branch agent {office.get('code')}",
    )
    from ditaknet.core.hr.attendance import record_presence_event

    await record_presence_event(
        employee_id=employee_id,
        event_type="seen",
        event_time=detected_at or _now(),
        source=presence_source,
        device_id=device_id,
        ip=ip_address,
        mac=mac_address,
        hostname=hostname,
        confidence=event_confidence,
        office_id=office_id,
    )
    result["updated"] = True
    return result


async def upsert_employee_presence_with_office(
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
    office_id: int | None = None,
    branch_agent_id: int | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Extend central presence with office and branch agent metadata."""
    if status not in STATUS_VALUES:
        raise ValueError(f"Invalid status: {status}")
    existing = await db.get_db()
    conn = existing
    rows = await conn.execute_fetchall(
        "SELECT * FROM employee_presence WHERE employee_id = ?", (employee_id,)
    )
    existing_presence = _to_dict(rows[0]) if rows else None
    old_status = str(existing_presence.get("status") or "") if existing_presence else ""
    now = _now()
    first_seen_at = existing_presence.get("first_seen_at") if existing_presence else now
    last_status_change_at = (
        existing_presence.get("last_status_change_at")
        if existing_presence and old_status == status
        else now
    )
    await conn.execute(
        """INSERT INTO employee_presence
           (employee_id, device_id, status, connection_type, confidence,
            current_ip, detected_mac, detected_hostname, source, first_seen_at,
            last_seen_at, last_status_change_at, notes, office_id, branch_agent_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
             notes = excluded.notes,
             office_id = excluded.office_id,
             branch_agent_id = excluded.branch_agent_id""",
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
            office_id,
            branch_agent_id,
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
    rows = await conn.execute_fetchall(
        "SELECT * FROM employee_presence WHERE employee_id = ?", (employee_id,)
    )
    return _to_dict(rows[0]) if rows else {}


async def ingest_presence_events_batch(
    office: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    branch_agent_id: int | None = None,
) -> dict[str, Any]:
    processed = 0
    matched = 0
    conflicts = 0
    for event in events:
        result = await ingest_presence_event(
            office,
            branch_agent_id=branch_agent_id,
            office_code=str(event.get("office_code") or office.get("code") or ""),
            detected_at=str(event.get("detected_at") or _now()),
            mac_address=str(event.get("mac_address") or ""),
            hostname=str(event.get("hostname") or ""),
            ip_address=str(event.get("ip_address") or ""),
            source=str(event.get("source") or "branch_agent"),
            confidence=str(event.get("confidence") or "low"),
            device_fingerprint=str(event.get("device_fingerprint") or ""),
            agent_version=str(event.get("agent_version") or ""),
            payload=event,
        )
        processed += 1
        if result.get("matched"):
            matched += 1
        if result.get("conflict"):
            conflicts += 1
    await create_privacy_audit_log(
        actor_user_id=f"branch:{office.get('code')}",
        action="branch_presence_batch",
        details=f"processed={processed} matched={matched} conflicts={conflicts}",
    )
    return {"processed": processed, "matched": matched, "conflicts": conflicts}
