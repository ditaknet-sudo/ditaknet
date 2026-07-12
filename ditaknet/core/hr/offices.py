"""Office / branch registry for multi-office presence."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from ditaknet import database as db
from ditaknet.core.employee_presence import create_privacy_audit_log
from ditaknet.core.hr.access import enforce_hr_access
from ditaknet.core.licensing import hash_secret, license_service


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _to_dict(row: Any) -> dict[str, Any]:
    return dict(row)


def hash_branch_token(raw_token: str) -> str:
    return hash_secret(raw_token)


def generate_branch_token() -> str:
    return secrets.token_urlsafe(32)


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


def branch_agent_online(last_seen_at: Any, *, grace_minutes: int = 5) -> bool:
    seen = _parse_dt(last_seen_at)
    if not seen:
        return False
    return datetime.now(UTC) - seen <= timedelta(minutes=grace_minutes)


async def list_offices(*, status: str = "") -> list[dict[str, Any]]:
    await license_service.enforce_multi_office_access()
    conn = await db.get_db()
    sql = """
        SELECT o.*,
          (SELECT COUNT(*) FROM employee_presence p
           WHERE p.office_id = o.id AND p.status = 'onsite') AS onsite_count,
          (SELECT COUNT(*) FROM employees e
           WHERE e.default_office_id = o.id AND e.status = 'active') AS default_employee_count
        FROM offices o
    """
    params: list[Any] = []
    if status:
        sql += " WHERE o.status = ?"
        params.append(status)
    sql += " ORDER BY o.name"
    rows = await conn.execute_fetchall(sql, params)
    result = []
    for row in rows:
        item = _to_dict(row)
        item["branch_agent_online"] = branch_agent_online(item.get("last_agent_seen_at"))
        item["has_branch_token"] = bool(str(item.get("branch_token_hash") or "").strip())
        result.append(item)
    return result


async def get_office(office_id: int) -> dict[str, Any] | None:
    await license_service.enforce_multi_office_access()
    conn = await db.get_db()
    rows = await conn.execute_fetchall(
        """
        SELECT o.*,
          (SELECT COUNT(*) FROM employee_presence p
           WHERE p.office_id = o.id AND p.status = 'onsite') AS onsite_count
        FROM offices o WHERE o.id = ?
        """,
        (office_id,),
    )
    if not rows:
        return None
    item = _to_dict(rows[0])
    item["branch_agent_online"] = branch_agent_online(item.get("last_agent_seen_at"))
    item["has_branch_token"] = bool(str(item.get("branch_token_hash") or "").strip())
    return item


async def get_office_by_code(code: str) -> dict[str, Any] | None:
    conn = await db.get_db()
    rows = await conn.execute_fetchall("SELECT * FROM offices WHERE code = ?", (code.strip().lower(),))
    return _to_dict(rows[0]) if rows else None


async def create_office(
    *,
    name: str,
    code: str,
    address: str = "",
    city: str = "",
    timezone: str = "UTC",
    subnet_cidr: str = "",
    public_ip: str = "",
    status: str = "active",
    actor: str = "system",
    issue_token: bool = True,
) -> dict[str, Any]:
    await enforce_hr_access()
    await license_service.enforce_office_create()
    normalized_code = code.strip().lower()
    if not normalized_code:
        raise ValueError("Office code is required")
    existing = await get_office_by_code(normalized_code)
    if existing:
        raise ValueError("Office code already exists")
    raw_token = generate_branch_token() if issue_token else ""
    token_hash = hash_branch_token(raw_token) if raw_token else ""
    now = _now()
    conn = await db.get_db()
    cursor = await conn.execute(
        """INSERT INTO offices
           (name, code, address, city, timezone, subnet_cidr, public_ip, status,
            branch_token_hash, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            name.strip(),
            normalized_code,
            address.strip(),
            city.strip(),
            timezone.strip() or "UTC",
            subnet_cidr.strip(),
            public_ip.strip(),
            status,
            token_hash,
            now,
            now,
        ),
    )
    await conn.commit()
    office_id = int(cursor.lastrowid)
    await create_privacy_audit_log(
        actor_user_id=actor,
        action="office_created",
        details=f"id={office_id} code={normalized_code}",
    )
    office = await get_office(office_id)
    if office and raw_token:
        office["branch_token_once"] = raw_token
    return office or {}


async def update_office(
    office_id: int,
    *,
    name: str | None = None,
    address: str | None = None,
    city: str | None = None,
    timezone: str | None = None,
    subnet_cidr: str | None = None,
    public_ip: str | None = None,
    status: str | None = None,
    actor: str = "system",
) -> dict[str, Any]:
    await license_service.enforce_multi_office_access()
    office = await get_office(office_id)
    if not office:
        raise ValueError("Office not found")
    fields: list[str] = []
    params: list[Any] = []
    for key, value in (
        ("name", name),
        ("address", address),
        ("city", city),
        ("timezone", timezone),
        ("subnet_cidr", subnet_cidr),
        ("public_ip", public_ip),
        ("status", status),
    ):
        if value is not None:
            fields.append(f"{key} = ?")
            params.append(value.strip() if isinstance(value, str) else value)
    if not fields:
        return office
    fields.append("updated_at = ?")
    params.append(_now())
    params.append(office_id)
    conn = await db.get_db()
    await conn.execute(f"UPDATE offices SET {', '.join(fields)} WHERE id = ?", params)
    await conn.commit()
    await create_privacy_audit_log(
        actor_user_id=actor,
        action="office_updated",
        details=f"id={office_id}",
    )
    return await get_office(office_id) or {}


async def rotate_branch_token(office_id: int, *, actor: str = "system") -> dict[str, Any]:
    await license_service.enforce_multi_office_access()
    office = await get_office(office_id)
    if not office:
        raise ValueError("Office not found")
    raw_token = generate_branch_token()
    token_hash = hash_branch_token(raw_token)
    conn = await db.get_db()
    await conn.execute(
        "UPDATE offices SET branch_token_hash = ?, updated_at = ? WHERE id = ?",
        (token_hash, _now(), office_id),
    )
    await conn.commit()
    await create_privacy_audit_log(
        actor_user_id=actor,
        action="branch_token_rotated",
        details=f"office_id={office_id}",
    )
    updated = await get_office(office_id) or {}
    updated["branch_token_once"] = raw_token
    return updated


async def disable_branch(office_id: int, *, actor: str = "system") -> dict[str, Any]:
    return await update_office(office_id, status="inactive", actor=actor)


async def offices_dashboard_summary() -> dict[str, Any]:
    await license_service.enforce_multi_office_access()
    offices = await list_offices(status="active")
    online = sum(1 for o in offices if o.get("branch_agent_online"))
    offline = len(offices) - online
    onsite_by_office = [
        {"office_id": o["id"], "name": o["name"], "code": o["code"], "count": int(o.get("onsite_count") or 0)}
        for o in offices
    ]
    return {
        "total_offices": len(offices),
        "online_branch_agents": online,
        "offline_branch_agents": offline,
        "onsite_by_office": onsite_by_office,
    }


async def list_office_agents(office_id: int) -> list[dict[str, Any]]:
    await license_service.enforce_multi_office_access()
    conn = await db.get_db()
    rows = await conn.execute_fetchall(
        "SELECT * FROM branch_agents WHERE office_id = ? ORDER BY last_heartbeat_at DESC",
        (office_id,),
    )
    return [_to_dict(row) for row in rows]
