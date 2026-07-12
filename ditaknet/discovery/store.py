"""Discovery persistence: monitored networks, inventory, settings, demo cleanup."""

from __future__ import annotations

import ipaddress
import json
from datetime import UTC, datetime
from typing import Any, Optional

import aiosqlite

from ditaknet.database import _now, _row_to_dict, get_db, get_app_setting, set_app_setting

DEMO_DISCOVERY_SOURCES = frozenset({"demo", "mock", "sample", "fake", "test_fixture"})

_DISCOVERY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS monitored_networks (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT    NOT NULL,
    vlan_id              TEXT    NOT NULL DEFAULT '',
    cidr                 TEXT    NOT NULL,
    description          TEXT    NOT NULL DEFAULT '',
    enabled              INTEGER NOT NULL DEFAULT 1,
    scan_mode            TEXT    NOT NULL DEFAULT 'normal',
    auto_refresh_enabled INTEGER NOT NULL DEFAULT 1,
    last_scan_id         INTEGER,
    last_scan_at         TEXT,
    created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_monitored_networks_cidr ON monitored_networks(cidr);

CREATE TABLE IF NOT EXISTS discovery_inventory (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    subnet              TEXT    NOT NULL,
    ip_address          TEXT    NOT NULL,
    mac_address         TEXT    NOT NULL DEFAULT '',
    hostname            TEXT    NOT NULL DEFAULT '',
    vendor              TEXT    NOT NULL DEFAULT '',
    detected_type       TEXT    NOT NULL DEFAULT 'unknown',
    confidence          INTEGER NOT NULL DEFAULT 0,
    open_ports          TEXT    NOT NULL DEFAULT '[]',
    device_state        TEXT    NOT NULL DEFAULT 'active',
    first_seen_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    last_seen_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    last_scan_id        INTEGER,
    imported_host_id    INTEGER,
    discovery_source    TEXT    NOT NULL DEFAULT '',
    evidence_json       TEXT    NOT NULL DEFAULT '[]',
    ignored             INTEGER NOT NULL DEFAULT 0,
    UNIQUE(subnet, ip_address)
);
CREATE INDEX IF NOT EXISTS idx_discovery_inventory_state ON discovery_inventory(device_state, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_discovery_inventory_subnet ON discovery_inventory(subnet, ip_address);

CREATE TABLE IF NOT EXISTS discovery_change_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subnet          TEXT    NOT NULL,
    ip_address      TEXT    NOT NULL,
    mac_address     TEXT    NOT NULL DEFAULT '',
    change_type     TEXT    NOT NULL,
    old_value       TEXT,
    new_value       TEXT,
    scan_id         INTEGER,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_discovery_change_events_created ON discovery_change_events(created_at DESC);
"""


async def ensure_discovery_schema(connection: aiosqlite.Connection) -> None:
    await connection.executescript(_DISCOVERY_SCHEMA_SQL)
    await _migrate_legacy_discovery_subnets()


async def _migrate_legacy_discovery_subnets() -> None:
    raw = await get_app_setting("discovery_refresh_subnets", "[]")
    try:
        legacy = [str(item) for item in json.loads(raw or "[]") if item]
    except (TypeError, json.JSONDecodeError):
        legacy = []
    if not legacy:
        return
    existing = await list_monitored_networks(enabled_only=False)
    existing_cidrs = {str(n.get("cidr") or "") for n in existing}
    for idx, cidr in enumerate(legacy):
        if cidr in existing_cidrs:
            continue
        await create_monitored_network(
            name=f"Network {idx + 1}",
            cidr=cidr,
            vlan_id="",
            description="Migrated from legacy discovery settings",
            scan_mode="normal",
            enabled=True,
            auto_refresh_enabled=True,
        )


def filter_devices_by_monitored_subnets(
    devices: list[dict],
    monitored_subnets: list[str],
    *,
    limit: int = 500,
) -> list[dict]:
    if not monitored_subnets:
        return []
    networks = []
    for cidr in monitored_subnets:
        try:
            networks.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            continue
    if not networks:
        return []
    filtered: list[dict] = []
    for device in devices:
        ip = str(device.get("ip_address") or "")
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if any(addr in net for net in networks):
            filtered.append(device)
        if len(filtered) >= limit:
            break
    return filtered


async def list_monitored_networks(*, enabled_only: bool = False) -> list[dict]:
    db_conn = await get_db()
    if enabled_only:
        rows = await db_conn.execute_fetchall(
            "SELECT * FROM monitored_networks WHERE enabled = 1 ORDER BY name, id"
        )
    else:
        rows = await db_conn.execute_fetchall(
            "SELECT * FROM monitored_networks ORDER BY name, id"
        )
    return [_row_to_dict(r) for r in rows]


async def get_monitored_network(network_id: int) -> Optional[dict]:
    db_conn = await get_db()
    rows = await db_conn.execute_fetchall(
        "SELECT * FROM monitored_networks WHERE id = ?", (network_id,)
    )
    return _row_to_dict(rows[0]) if rows else None


async def count_monitored_networks() -> int:
    db_conn = await get_db()
    rows = await db_conn.execute_fetchall("SELECT COUNT(*) AS cnt FROM monitored_networks")
    return int(rows[0]["cnt"] if rows else 0)


async def create_monitored_network(
    *,
    name: str,
    cidr: str,
    vlan_id: str = "",
    description: str = "",
    scan_mode: str = "normal",
    enabled: bool = True,
    auto_refresh_enabled: bool = True,
) -> dict:
    db_conn = await get_db()
    now = _now()
    cursor = await db_conn.execute(
        """INSERT INTO monitored_networks
           (name, vlan_id, cidr, description, enabled, scan_mode, auto_refresh_enabled, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            name.strip(),
            vlan_id.strip(),
            cidr.strip(),
            description.strip(),
            int(enabled),
            scan_mode,
            int(auto_refresh_enabled),
            now,
        ),
    )
    await db_conn.commit()
    await register_discovery_subnet(cidr)
    return (await get_monitored_network(int(cursor.lastrowid))) or {}


async def update_monitored_network(network_id: int, **fields: Any) -> Optional[dict]:
    db_conn = await get_db()
    sets, vals = [], []
    for key in (
        "name",
        "vlan_id",
        "cidr",
        "description",
        "enabled",
        "scan_mode",
        "auto_refresh_enabled",
        "last_scan_id",
        "last_scan_at",
    ):
        if key not in fields or fields[key] is None:
            continue
        val = fields[key]
        if key in {"enabled", "auto_refresh_enabled"}:
            val = int(val)
        sets.append(f"{key} = ?")
        vals.append(val)
    if not sets:
        return await get_monitored_network(network_id)
    sets.append("updated_at = ?")
    vals.append(_now())
    vals.append(network_id)
    await db_conn.execute(
        f"UPDATE monitored_networks SET {', '.join(sets)} WHERE id = ?", vals
    )
    await db_conn.commit()
    net = await get_monitored_network(network_id)
    if net and net.get("cidr"):
        await register_discovery_subnet(str(net["cidr"]))
    return net


async def delete_monitored_network(network_id: int) -> bool:
    db_conn = await get_db()
    cur = await db_conn.execute("DELETE FROM monitored_networks WHERE id = ?", (network_id,))
    await db_conn.commit()
    await _sync_refresh_subnets_from_monitored()
    return bool(cur.rowcount)


async def register_discovery_subnet(subnet: str) -> None:
    subnet = (subnet or "").strip()
    if not subnet:
        return
    raw = await get_app_setting("discovery_refresh_subnets", "[]")
    try:
        subnets = [str(item) for item in json.loads(raw or "[]") if item]
    except (TypeError, json.JSONDecodeError):
        subnets = []
    if subnet not in subnets:
        subnets.append(subnet)
    await set_app_setting("discovery_refresh_subnets", json.dumps(subnets))


async def _sync_refresh_subnets_from_monitored() -> None:
    nets = await list_monitored_networks(enabled_only=True)
    cidrs = [str(n.get("cidr") or "") for n in nets if n.get("auto_refresh_enabled")]
    await set_app_setting("discovery_refresh_subnets", json.dumps(cidrs))


async def list_discovery_monitored_subnets() -> list[str]:
    nets = await list_monitored_networks(enabled_only=True)
    if nets:
        return [str(n.get("cidr") or "") for n in nets if n.get("cidr")]
    raw = await get_app_setting("discovery_refresh_subnets", "[]")
    try:
        return [str(item) for item in json.loads(raw or "[]") if item]
    except (TypeError, json.JSONDecodeError):
        return []


async def set_monitored_network_last_scan(network_id: int, scan_id: int) -> None:
    await update_monitored_network(
        network_id,
        last_scan_id=scan_id,
        last_scan_at=_now(),
    )


async def create_discovery_change_event(
    *,
    subnet: str,
    ip_address: str,
    change_type: str,
    mac_address: str = "",
    old_value: str | None = None,
    new_value: str | None = None,
    scan_id: int | None = None,
) -> dict:
    db_conn = await get_db()
    cursor = await db_conn.execute(
        """INSERT INTO discovery_change_events
           (subnet, ip_address, mac_address, change_type, old_value, new_value, scan_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (subnet, ip_address, mac_address, change_type, old_value, new_value, scan_id, _now()),
    )
    await db_conn.commit()
    rows = await db_conn.execute_fetchall(
        "SELECT * FROM discovery_change_events WHERE id = ?", (cursor.lastrowid,)
    )
    return _row_to_dict(rows[0])


async def sync_discovery_inventory_device(
    *,
    subnet: str,
    scan_id: int,
    ip_address: str,
    mac_address: str = "",
    hostname: str = "",
    vendor: str = "",
    detected_type: str = "unknown",
    confidence: int = 0,
    open_ports: str = "[]",
    discovery_source: str = "",
    evidence_json: str = "[]",
) -> dict:
    if discovery_source.lower() in DEMO_DISCOVERY_SOURCES:
        return {}
    db_conn = await get_db()
    now = _now()
    rows = await db_conn.execute_fetchall(
        "SELECT * FROM discovery_inventory WHERE subnet = ? AND ip_address = ?",
        (subnet, ip_address),
    )
    existing = _row_to_dict(rows[0]) if rows else None
    if existing:
        if existing.get("imported_host_id"):
            state = "imported"
        elif existing.get("ignored"):
            state = "ignored"
        elif existing.get("device_state") == "missing":
            state = "active"
            await create_discovery_change_event(
                subnet=subnet,
                ip_address=ip_address,
                change_type="device_returned",
                mac_address=mac_address,
                scan_id=scan_id,
            )
        else:
            state = str(existing.get("device_state") or "active")
            if state == "new":
                state = "active"
        old_ports = str(existing.get("open_ports") or "[]")
        if old_ports != open_ports:
            await create_discovery_change_event(
                subnet=subnet,
                ip_address=ip_address,
                change_type="ports_changed",
                old_value=old_ports,
                new_value=open_ports,
                scan_id=scan_id,
            )
        old_type = str(existing.get("detected_type") or "")
        if old_type != detected_type:
            await create_discovery_change_event(
                subnet=subnet,
                ip_address=ip_address,
                change_type="type_changed",
                old_value=old_type,
                new_value=detected_type,
                scan_id=scan_id,
            )
        await db_conn.execute(
            """UPDATE discovery_inventory SET
               mac_address = ?, hostname = ?, vendor = ?, detected_type = ?,
               confidence = ?, open_ports = ?, device_state = ?, last_seen_at = ?,
               last_scan_id = ?, discovery_source = ?, evidence_json = ?
               WHERE subnet = ? AND ip_address = ?""",
            (
                mac_address,
                hostname,
                vendor,
                detected_type,
                confidence,
                open_ports,
                state,
                now,
                scan_id,
                discovery_source,
                evidence_json,
                subnet,
                ip_address,
            ),
        )
    else:
        await create_discovery_change_event(
            subnet=subnet,
            ip_address=ip_address,
            change_type="new_device",
            mac_address=mac_address,
            scan_id=scan_id,
        )
        await db_conn.execute(
            """INSERT INTO discovery_inventory
               (subnet, ip_address, mac_address, hostname, vendor, detected_type,
                confidence, open_ports, device_state, first_seen_at, last_seen_at,
                last_scan_id, discovery_source, evidence_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?, ?, ?)""",
            (
                subnet,
                ip_address,
                mac_address,
                hostname,
                vendor,
                detected_type,
                confidence,
                open_ports,
                now,
                now,
                scan_id,
                discovery_source,
                evidence_json,
            ),
        )
    await db_conn.commit()
    rows = await db_conn.execute_fetchall(
        "SELECT * FROM discovery_inventory WHERE subnet = ? AND ip_address = ?",
        (subnet, ip_address),
    )
    return _row_to_dict(rows[0]) if rows else {}


async def mark_inventory_devices_missing(subnet: str, seen_ips: set[str], scan_id: int) -> int:
    db_conn = await get_db()
    rows = await db_conn.execute_fetchall(
        """SELECT * FROM discovery_inventory
           WHERE subnet = ? AND device_state NOT IN ('ignored', 'imported', 'missing')
             AND LOWER(discovery_source) NOT IN ('demo', 'mock', 'sample', 'fake', 'test_fixture')""",
        (subnet,),
    )
    count = 0
    for row in rows:
        item = _row_to_dict(row)
        ip = str(item.get("ip_address") or "")
        if ip in seen_ips:
            continue
        await db_conn.execute(
            "UPDATE discovery_inventory SET device_state = 'missing' WHERE id = ?",
            (item["id"],),
        )
        await create_discovery_change_event(
            subnet=subnet,
            ip_address=ip,
            change_type="device_missing",
            mac_address=str(item.get("mac_address") or ""),
            scan_id=scan_id,
        )
        count += 1
    await db_conn.commit()
    return count


async def list_discovery_inventory(
    *,
    device_state: str | None = None,
    subnet: str | None = None,
    subnets: list[str] | None = None,
    limit: int = 50,
    hide_demo: bool = True,
) -> list[dict]:
    db_conn = await get_db()
    clauses = ["1=1"]
    params: list[Any] = []
    if device_state:
        clauses.append("device_state = ?")
        params.append(device_state)
    if subnet:
        clauses.append("subnet = ?")
        params.append(subnet)
    if subnets:
        placeholders = ", ".join("?" for _ in subnets)
        clauses.append(f"subnet IN ({placeholders})")
        params.extend(subnets)
    if hide_demo:
        placeholders = ", ".join("?" for _ in DEMO_DISCOVERY_SOURCES)
        clauses.append(f"LOWER(discovery_source) NOT IN ({placeholders})")
        params.extend(DEMO_DISCOVERY_SOURCES)
    params.append(limit)
    rows = await db_conn.execute_fetchall(
        f"""SELECT * FROM discovery_inventory
            WHERE {' AND '.join(clauses)}
            ORDER BY last_seen_at DESC
            LIMIT ?""",
        tuple(params),
    )
    return [_row_to_dict(r) for r in rows]


async def list_discovery_change_events(limit: int = 50) -> list[dict]:
    db_conn = await get_db()
    rows = await db_conn.execute_fetchall(
        """SELECT * FROM discovery_change_events
           ORDER BY created_at DESC
           LIMIT ?""",
        (limit,),
    )
    return [_row_to_dict(r) for r in rows]


async def ignore_discovery_inventory_device(device_id: int) -> Optional[dict]:
    db_conn = await get_db()
    await db_conn.execute(
        "UPDATE discovery_inventory SET device_state = 'ignored', ignored = 1 WHERE id = ?",
        (device_id,),
    )
    await db_conn.commit()
    rows = await db_conn.execute_fetchall(
        "SELECT * FROM discovery_inventory WHERE id = ?", (device_id,)
    )
    return _row_to_dict(rows[0]) if rows else None


async def mark_discovery_inventory_device_imported(
    device_id: int,
    host_id: int,
) -> Optional[dict]:
    db_conn = await get_db()
    await db_conn.execute(
        """UPDATE discovery_inventory
           SET device_state = 'imported', imported_host_id = ?, ignored = 0
           WHERE id = ?""",
        (host_id, device_id),
    )
    await db_conn.commit()
    rows = await db_conn.execute_fetchall(
        "SELECT * FROM discovery_inventory WHERE id = ?", (device_id,)
    )
    return _row_to_dict(rows[0]) if rows else None


async def mark_discovery_inventory_address_imported(
    *,
    ip_address: str,
    host_id: int,
    scan_id: int | None = None,
) -> Optional[dict]:
    db_conn = await get_db()
    params: list[Any] = [host_id, ip_address]
    scan_clause = ""
    if scan_id:
        scan_clause = " AND last_scan_id = ?"
        params.append(scan_id)
    await db_conn.execute(
        f"""UPDATE discovery_inventory
            SET device_state = 'imported', imported_host_id = ?, ignored = 0
            WHERE ip_address = ?{scan_clause}""",
        tuple(params),
    )
    await db_conn.commit()
    rows = await db_conn.execute_fetchall(
        """SELECT * FROM discovery_inventory
           WHERE ip_address = ?
           ORDER BY last_seen_at DESC
           LIMIT 1""",
        (ip_address,),
    )
    return _row_to_dict(rows[0]) if rows else None


async def cleanup_demo_discovery_data() -> dict[str, int]:
    db_conn = await get_db()
    placeholders = ", ".join("?" for _ in DEMO_DISCOVERY_SOURCES)
    params = list(DEMO_DISCOVERY_SOURCES)
    cur1 = await db_conn.execute(
        f"DELETE FROM discovered_devices WHERE LOWER(discovery_source) IN ({placeholders})",
        params,
    )
    cur2 = await db_conn.execute(
        f"DELETE FROM discovery_inventory WHERE LOWER(discovery_source) IN ({placeholders})",
        params,
    )
    await db_conn.commit()
    return {
        "discovered_devices_removed": int(cur1.rowcount or 0),
        "inventory_removed": int(cur2.rowcount or 0),
    }


async def purge_unauthorized_discovery_records() -> dict[str, int]:
    """Remove inventory rows outside configured monitored subnets."""
    nets = await list_monitored_networks(enabled_only=False)
    if not nets:
        db_conn = await get_db()
        cur = await db_conn.execute("DELETE FROM discovery_inventory")
        await db_conn.commit()
        return {"inventory_removed": int(cur.rowcount or 0)}
    networks = []
    for net in nets:
        try:
            networks.append(ipaddress.ip_network(str(net.get("cidr") or ""), strict=False))
        except ValueError:
            continue
    db_conn = await get_db()
    rows = await db_conn.execute_fetchall("SELECT id, ip_address FROM discovery_inventory")
    removed = 0
    for row in rows:
        ip = str(row["ip_address"] or "")
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if not any(addr in n for n in networks):
            await db_conn.execute("DELETE FROM discovery_inventory WHERE id = ?", (row["id"],))
            removed += 1
    await db_conn.commit()
    return {"inventory_removed": removed}


async def get_discovery_settings() -> dict[str, Any]:
    return {
        "auto_refresh_enabled": (await get_app_setting("discovery_auto_refresh_enabled", "1")) == "1",
        "auto_import_enabled": (await get_app_setting("discovery_auto_import_enabled", "1")) == "1",
        "auto_import_skip_mobile": (await get_app_setting("discovery_auto_import_skip_mobile", "1")) == "1",
        "refresh_interval_minutes": int(
            await get_app_setting("discovery_refresh_interval_minutes", "10") or "10"
        ),
        "stale_after_minutes": int(
            await get_app_setting("discovery_stale_after_minutes", "30") or "30"
        ),
        "offline_after_minutes": int(
            await get_app_setting("discovery_offline_after_minutes", "60") or "60"
        ),
        "scan_mode": await get_app_setting("discovery_default_scan_mode", "normal") or "normal",
    }


async def update_discovery_settings(**fields: Any) -> dict[str, Any]:
    mapping = {
        "auto_refresh_enabled": "discovery_auto_refresh_enabled",
        "auto_import_enabled": "discovery_auto_import_enabled",
        "auto_import_skip_mobile": "discovery_auto_import_skip_mobile",
        "refresh_interval_minutes": "discovery_refresh_interval_minutes",
        "stale_after_minutes": "discovery_stale_after_minutes",
        "offline_after_minutes": "discovery_offline_after_minutes",
        "scan_mode": "discovery_default_scan_mode",
    }
    for key, setting_key in mapping.items():
        if key in fields and fields[key] is not None:
            val = fields[key]
            if key in {"auto_refresh_enabled", "auto_import_enabled", "auto_import_skip_mobile"}:
                val = "1" if val else "0"
            await set_app_setting(setting_key, str(val))
    return await get_discovery_settings()


async def get_last_discovery_refresh_at() -> str | None:
    return await get_app_setting("discovery_last_refresh_at")


async def set_last_discovery_refresh_at() -> None:
    await set_app_setting("discovery_last_refresh_at", _now())


async def apply_discovery_inventory_ageing(
    *,
    stale_after_minutes: int | None = None,
    offline_after_minutes: int | None = None,
) -> dict[str, int]:
    from ditaknet.config import settings

    stale_mins = stale_after_minutes or settings.discovery_stale_after_minutes
    offline_mins = offline_after_minutes or settings.discovery_offline_after_minutes
    now = datetime.now(UTC)
    db_conn = await get_db()
    rows = await db_conn.execute_fetchall(
        """SELECT * FROM discovery_inventory
           WHERE device_state IN ('missing', 'stale', 'active', 'new', 'seen')
             AND LOWER(discovery_source) NOT IN ('demo', 'mock', 'sample', 'fake', 'test_fixture')"""
    )
    stale_count = offline_count = 0
    for row in rows:
        item = _row_to_dict(row)
        last_seen = str(item.get("last_seen_at") or "")
        try:
            seen_at = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
            if seen_at.tzinfo is None:
                seen_at = seen_at.replace(tzinfo=UTC)
        except ValueError:
            continue
        age_mins = (now - seen_at).total_seconds() / 60.0
        state = str(item.get("device_state") or "")
        if state == "missing" and age_mins >= stale_mins:
            await db_conn.execute(
                "UPDATE discovery_inventory SET device_state = 'stale' WHERE id = ?",
                (item["id"],),
            )
            stale_count += 1
        elif state in {"missing", "stale"} and age_mins >= offline_mins:
            await db_conn.execute(
                "UPDATE discovery_inventory SET device_state = 'offline' WHERE id = ?",
                (item["id"],),
            )
            offline_count += 1
    await db_conn.commit()
    return {"stale": stale_count, "offline": offline_count}
