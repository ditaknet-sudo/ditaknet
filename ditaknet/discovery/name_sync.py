"""Sync host display names from latest discovery inventory."""

from __future__ import annotations

from loguru import logger

from ditaknet import database as db
from ditaknet.discovery import store as discovery_store
from ditaknet.discovery.naming import is_unnamed_label, resolve_device_name


async def refresh_host_names_from_discovery(*, limit: int = 500) -> dict[str, int]:
    """Update hosts that still use IP-only names when discovery has better labels."""
    inventory_rows = await discovery_store.list_discovery_inventory(limit=limit, hide_demo=True)
    by_ip = {
        str(row.get("ip_address") or "").strip(): row
        for row in inventory_rows
        if str(row.get("ip_address") or "").strip()
    }
    if not by_ip:
        return {"checked": 0, "updated": 0}

    conn = await db.get_db()
    host_rows = await conn.execute_fetchall("SELECT * FROM hosts ORDER BY id")
    checked = 0
    updated = 0

    for row in host_rows:
        if isinstance(row, dict):
            host = row
        else:
            host = {key: row[key] for key in row.keys()}
        checked += 1
        address = str(host.get("address") or "").strip()
        current_name = str(host.get("name") or "").strip()
        if not address or not is_unnamed_label(current_name, address):
            continue

        inv = by_ip.get(address)
        if not inv:
            continue

        new_name = resolve_device_name(
            hostname=str(inv.get("hostname") or host.get("hostname") or ""),
            vendor=str(inv.get("vendor") or ""),
            detected_type=str(inv.get("detected_type") or host.get("host_type") or "unknown"),
            ip_address=address,
            fallback_name=current_name,
        )
        if is_unnamed_label(new_name, address) or new_name == current_name:
            continue

        await db.update_host(
            int(host["id"]),
            name=new_name,
            hostname=str(inv.get("hostname") or host.get("hostname") or ""),
            mac_address=str(inv.get("mac_address") or host.get("mac_address") or ""),
            host_type=str(inv.get("detected_type") or host.get("host_type") or "unknown"),
        )
        updated += 1

    if updated:
        logger.info("Refreshed {} host name(s) from discovery inventory", updated)
    return {"checked": checked, "updated": updated}
