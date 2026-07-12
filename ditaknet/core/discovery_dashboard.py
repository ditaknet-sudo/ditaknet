"""Build Network Discovery page context (states A–E)."""

from __future__ import annotations

import json
from typing import Any

from ditaknet import database as db
from ditaknet.config import settings as app_settings
from ditaknet.core.licensing import license_service
from ditaknet.discovery import store as discovery_store
from ditaknet.discovery.diagnostics import (
    build_scan_diagnostics,
    parse_json_list,
    scan_result_payload,
)
from ditaknet.discovery.scheduler import discovery_scheduler
from ditaknet.discovery.scan_state import merge_progress, parse_subnets, scan_summary


def _parse_evidence(item: dict) -> list[str]:
    raw = item.get("evidence_json") or item.get("raw_metadata_json") or "[]"
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        return [str(v) for v in data.get("evidence") or data.get("signals") or []]
    if isinstance(data, list):
        return [str(x) for x in data]
    return []


def enrich_inventory_row(item: dict) -> dict:
    row = dict(item)
    row["evidence"] = _parse_evidence(item)
    try:
        row["open_ports_list"] = json.loads(item.get("open_ports") or "[]")
    except (TypeError, json.JSONDecodeError):
        row["open_ports_list"] = []
    return row


def _scan_is_active(scan: dict[str, Any] | None) -> bool:
    return bool(scan and scan.get("status") in {"pending", "running"})


def _enrich_scan(scan: dict[str, Any], progress: dict[str, Any] | None = None) -> dict[str, Any]:
    progress_state = merge_progress(scan, progress)
    summary = scan_summary(scan, progress_state)
    diagnostics = build_scan_diagnostics(scan, progress_state)
    probe_methods = parse_json_list(scan.get("probe_methods_json"))
    return {
        **scan,
        **summary,
        "progress": progress_state,
        "diagnostics": diagnostics,
        "probe_methods": probe_methods,
        "subnets_label": ", ".join(parse_subnets(scan)) or "-",
        "error_count": summary.get("failed_probes") or 0,
    }


async def build_discovery_page_context(
    *,
    selected_scan_id: int | None = None,
    active_tab: str = "networks",
) -> dict[str, Any]:
    license_status = await license_service.status()
    networks = await discovery_store.list_monitored_networks()
    monitored_cidrs = [str(n.get("cidr") or "") for n in networks if n.get("cidr")]
    scans = await db.list_discovery_scans(limit=20)

    active_scan = None
    active_progress: dict[str, Any] = {}
    for scan in scans:
        if scan.get("status") in {"pending", "running"}:
            active_scan = scan
            active_progress = discovery_scheduler.get_progress(int(scan["id"]))
            break

    enriched_scans = [
        _enrich_scan(
            scan,
            discovery_scheduler.get_progress(int(scan["id"])) if _scan_is_active(scan) else None,
        )
        for scan in scans
    ]
    enriched_by_id = {int(scan["id"]): scan for scan in enriched_scans}

    if selected_scan_id is None and active_scan:
        selected_scan_id = int(active_scan["id"])
    if selected_scan_id is None and networks:
        for net in networks:
            if net.get("last_scan_id"):
                selected_scan_id = int(net["last_scan_id"])
                break
    if selected_scan_id is None and scans and networks:
        completed = [s for s in scans if s.get("status") == "completed"]
        if completed:
            selected_scan_id = int(completed[0]["id"])

    scan_devices: list[dict] = []
    selected_scan = None
    selected_scan_result: dict[str, Any] | None = None
    selected_scan_diagnostics: list[dict[str, str]] = []
    if selected_scan_id:
        selected_scan = await db.get_discovery_scan(selected_scan_id)
        if selected_scan:
            if networks:
                scan_devices = await db.list_discovered_devices(
                    scan_id=selected_scan_id,
                    hide_demo=True,
                )
            for device in scan_devices:
                meta = device.get("raw_metadata_json") or "{}"
                try:
                    parsed = json.loads(meta)
                except (TypeError, json.JSONDecodeError):
                    parsed = {}
                device["evidence"] = parsed.get("evidence") or parsed.get("signals") or []
                try:
                    device["open_ports_list"] = json.loads(device.get("open_ports") or "[]")
                except (TypeError, json.JSONDecodeError):
                    device["open_ports_list"] = []
            live_progress = (
                discovery_scheduler.get_progress(selected_scan_id)
                if _scan_is_active(selected_scan)
                else None
            )
            selected_scan_result = scan_result_payload(
                selected_scan,
                devices=scan_devices,
                live_progress=live_progress,
            )
            selected_scan_diagnostics = selected_scan_result["diagnostics"]
            selected_scan = {
                **_enrich_scan(selected_scan, live_progress),
                **selected_scan_result,
                "id": selected_scan["id"],
            }

    inventory = await discovery_store.list_discovery_inventory(
        subnets=monitored_cidrs or None,
        limit=500,
        hide_demo=True,
    )
    inventory_rows = [enrich_inventory_row(i) for i in inventory]

    if not networks:
        page_state = "not_configured"
    elif selected_scan and selected_scan.get("status") in {"pending", "running"}:
        page_state = "scan_running"
    elif active_scan:
        page_state = "scan_running"
    elif selected_scan and selected_scan.get("status") == "failed":
        page_state = "scan_failed"
    elif not any(n.get("last_scan_id") for n in networks):
        page_state = "configured_no_scan"
    elif selected_scan and selected_scan.get("status") == "completed" and not scan_devices:
        page_state = "scan_empty"
    else:
        page_state = "ready"

    setup_complete = await db.is_setup_complete()
    counts = {
        "networks": len(networks),
        "active": sum(1 for i in inventory_rows if i.get("device_state") in {"active", "seen"}),
        "new": sum(1 for i in inventory_rows if i.get("device_state") == "new"),
        "stale": sum(1 for i in inventory_rows if i.get("device_state") in {"stale", "missing"}),
        "offline": sum(1 for i in inventory_rows if i.get("device_state") == "offline"),
        "imported": sum(1 for i in inventory_rows if i.get("device_state") == "imported"),
    }

    network_cards = []
    for net in networks:
        cidr = str(net.get("cidr") or "")
        net_inventory = [i for i in inventory_rows if i.get("subnet") == cidr]
        last_scan = enriched_by_id.get(int(net["last_scan_id"])) if net.get("last_scan_id") else None
        network_cards.append(
            {
                **net,
                "found_devices": len(net_inventory),
                "new_devices": sum(1 for i in net_inventory if i.get("device_state") == "new"),
                "stale_devices": sum(
                    1
                    for i in net_inventory
                    if i.get("device_state") in {"stale", "missing", "offline"}
                ),
                "last_scan": last_scan,
                "last_scan_status": (last_scan or {}).get("status") or "",
                "last_scan_found": (last_scan or {}).get("found") or 0,
                "last_scan_failed_probes": (last_scan or {}).get("failed_probes") or 0,
            }
        )

    last_scan = enriched_scans[0] if enriched_scans else None
    settings = await discovery_store.get_discovery_settings()
    license_discovery_locked = (
        not bool(license_status.get("write_allowed", True))
        or not bool(license_status.get("operational_access", True))
        or license_status.get("max_discovery_subnets") == 0
        or not app_settings.discovery_enabled
    )
    discovery_lock_reason = ""
    if not app_settings.discovery_enabled:
        discovery_lock_reason = "Network discovery is disabled in server configuration."
    elif not bool(license_status.get("operational_access", True)):
        discovery_lock_reason = "Current license status does not allow operational access."
    elif not bool(license_status.get("write_allowed", True)):
        discovery_lock_reason = "Current license is read-only or expired."
    elif license_status.get("max_discovery_subnets") == 0:
        discovery_lock_reason = "Current package does not include network discovery."

    return {
        "page_state": page_state,
        "active_tab": active_tab,
        "license": license_status,
        "networks": network_cards,
        "monitored_cidrs": monitored_cidrs,
        "scans": enriched_scans,
        "selected_scan": selected_scan,
        "selected_scan_id": selected_scan_id,
        "selected_scan_result": selected_scan_result,
        "selected_scan_diagnostics": selected_scan_diagnostics,
        "scan_devices": scan_devices,
        "inventory": inventory_rows,
        "new_devices": [i for i in inventory_rows if i.get("device_state") == "new"],
        "imported_devices": [i for i in inventory_rows if i.get("device_state") == "imported"],
        "change_events": await discovery_store.list_discovery_change_events(limit=30),
        "counts": counts,
        "active_scan": enriched_by_id.get(int(active_scan["id"])) if active_scan else None,
        "active_progress": merge_progress(active_scan or {}, active_progress) if active_scan else {},
        "last_scan": last_scan,
        "settings": settings,
        "setup_complete": setup_complete,
        "last_refresh_at": await discovery_store.get_last_discovery_refresh_at(),
        "license_discovery_locked": license_discovery_locked,
        "discovery_lock_reason": discovery_lock_reason,
    }
