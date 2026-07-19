"""Persist and query discovered device inventory."""

from __future__ import annotations

import json
from typing import Any, Optional

from ditaknet import database as db
from ditaknet.discovery.scanner import DiscoveredHost


async def upsert_discovered_device(scan_id: int, host: DiscoveredHost) -> dict[str, Any]:
    """Insert or update a discovered device row for *scan_id*."""
    return await db.upsert_discovered_device(
        scan_id=scan_id,
        ip_address=host.ip_address,
        mac_address=host.mac_address,
        hostname=host.hostname,
        vendor=host.vendor,
        open_ports=json.dumps(host.open_ports),
        detected_services=json.dumps(host.detected_services),
        detected_type=host.detected_type,
        confidence=host.confidence,
        discovery_source=host.discovery_source,
        raw_metadata_json=json.dumps(host.raw_metadata),
    )


async def sync_network_inventory(
    subnet: str,
    scan_id: int,
    host: DiscoveredHost,
) -> dict[str, Any]:
    evidence = host.raw_metadata.get("evidence") or []
    return await db.sync_discovery_inventory_device(
        subnet=subnet,
        scan_id=scan_id,
        ip_address=host.ip_address,
        mac_address=host.mac_address,
        hostname=host.hostname,
        vendor=host.vendor,
        detected_type=host.detected_type,
        confidence=host.confidence,
        open_ports=json.dumps(host.open_ports),
        discovery_source=host.discovery_source,
        evidence_json=json.dumps(evidence),
    )


async def list_devices_for_scan(scan_id: int) -> list[dict[str, Any]]:
    return await db.list_discovered_devices(scan_id=scan_id)


async def list_all_discovered_devices(limit: int = 500) -> list[dict[str, Any]]:
    return await db.list_discovered_devices(limit=limit)


async def get_device(device_id: int) -> Optional[dict[str, Any]]:
    return await db.get_discovered_device(device_id)
