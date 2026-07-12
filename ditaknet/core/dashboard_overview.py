"""Dashboard overview aggregator — real KPIs, topology graph, problems, activity."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address, ip_network
from typing import Any

from ditaknet import database as db
from ditaknet.core.activity_service import activity_service
from ditaknet.core.build_metadata import build_metadata
from ditaknet.core.device_monitoring import (
    average_response_ms,
    build_heartbeat_bars,
    pick_primary_service,
    uptime_percent,
)
from ditaknet.core.licensing import license_service
from ditaknet.discovery import store as discovery_store
from ditaknet.i18n import translate

MAX_TOPOLOGY_DEVICES_PER_SUBNET = 6
MAX_TOPOLOGY_DISCOVERED_PER_SUBNET = 4
MAX_PROBLEMS = 8
MAX_ACTIVITY = 12

_STATE_SEVERITY = {"critical": 3, "warning": 2, "unknown": 1, "ok": 0, "disabled": -1}

_DEVICE_TYPES = frozenset(
    {
        "router",
        "gateway",
        "switch",
        "camera",
        "nvr",
        "server",
        "linux_server",
        "windows_server",
        "printer",
        "workstation",
        "ap",
        "nas",
    }
)


def _infer_segment(address: str) -> str:
    parts = str(address or "").split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        return f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
    return "unknown"


def _normalize_state(state: str | None) -> str:
    raw = str(state or "unknown").lower()
    if raw in {"ok", "warning", "critical", "unknown"}:
        return raw
    if raw in {"down", "offline", "failed"}:
        return "critical"
    return "unknown"


def _normalize_system_status(state: str | None) -> str:
    raw = str(state or "unknown").lower()
    if raw in {"healthy", "pass", "ok", "running"}:
        return "ok"
    if raw in {"degraded", "partial", "warning", "stopped"}:
        return "warning"
    if raw in {"unhealthy", "fail", "failed", "error", "critical"}:
        return "critical"
    return "unknown"


def _device_type(host: dict[str, Any]) -> str:
    dtype = str(host.get("host_type") or "unknown").lower()
    return dtype if dtype in _DEVICE_TYPES else "unknown"


def _network_label(net: dict[str, Any]) -> str:
    name = str(net.get("name") or "").strip()
    vlan = str(net.get("vlan_id") or "").strip()
    cidr = str(net.get("cidr") or "").strip()
    if name and not name.isdigit() and name.lower() not in {"lan", "network", "subnet"}:
        return name
    if vlan:
        return f"VLAN {vlan}"
    return cidr or "Subnet"


def _gateway_label(host: dict[str, Any]) -> str:
    name = str(host.get("name") or "").strip()
    host_type = str(host.get("host_type") or "").lower()
    if host_type in {"router", "gateway"} and name.lower().startswith("device-"):
        return "Gateway"
    return name or "Gateway"


def _discovered_type(device: dict[str, Any]) -> str:
    dtype = str(device.get("detected_type") or "unknown").lower()
    if dtype in {"linux_server", "windows_server"}:
        return "server"
    if dtype == "access_point":
        return "ap"
    return dtype if dtype in _DEVICE_TYPES else "unknown"


def _discovered_label(device: dict[str, Any]) -> str:
    hostname = str(device.get("hostname") or "").strip()
    if hostname:
        return hostname
    dtype = str(device.get("detected_type") or "unknown").replace("_", " ").strip()
    return dtype.title() if dtype and dtype != "unknown" else "Discovered device"


def _dedupe_discovered_devices(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for device in devices:
        ip = str(device.get("ip_address") or "").strip()
        mac = str(device.get("mac_address") or "").strip().lower()
        key = f"mac:{mac}" if mac else f"ip:{ip}"
        if not ip or key in seen:
            continue
        seen.add(key)
        unique.append(device)
    return unique


def _ip_in_cidr(address: str, cidr: str) -> bool:
    try:
        return ip_address(str(address)) in ip_network(str(cidr), strict=False)
    except ValueError:
        return False


def _subnet_id_for_ip(address: str, networks: list[dict[str, Any]]) -> str:
    for net in networks:
        cidr = str(net.get("cidr") or "")
        if cidr and _ip_in_cidr(address, cidr):
            return f"subnet-{net['id']}"
    return ""


def _subnet_node_id(cidr: str, networks: list[dict[str, Any]]) -> str:
    for net in networks:
        if str(net.get("cidr") or "") == cidr:
            return f"subnet-{net['id']}"
    safe = "".join(ch if ch.isalnum() else "-" for ch in cidr)[:48] or "unknown"
    return f"subnet-real-{safe}"


def _cidr_for_address(address: str, networks: list[dict[str, Any]]) -> str:
    for net in networks:
        cidr = str(net.get("cidr") or "")
        if cidr and _ip_in_cidr(address, cidr):
            return cidr
    return _infer_segment(address)


def _inventory_state(state: str | None) -> str:
    raw = str(state or "").lower()
    if raw in {"active", "seen", "imported"}:
        return "ok"
    if raw in {"new", "unknown"}:
        return "unknown"
    if raw in {"missing", "stale"}:
        return "warning"
    if raw in {"offline"}:
        return "critical"
    return "unknown"


def _display_state(state: str | None) -> str:
    normalized = _normalize_state(state)
    if normalized == "ok":
        return "Up"
    if normalized == "warning":
        return "Warning"
    if normalized == "critical":
        return "Down"
    return "Unknown"


def _format_percent(value: float | None) -> str:
    if value is None:
        return "--"
    if value >= 99.995:
        return "100%"
    return f"{value:.2f}%"


def _format_ms(value: float | int | None) -> str:
    if value is None:
        return "--"
    try:
        return f"{int(round(float(value)))} ms"
    except (TypeError, ValueError):
        return "--"


def _safe_ms(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _response_points(rows: list[dict[str, Any]], *, limit: int = 44) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for row in reversed(rows):
        ms = _safe_ms(row.get("response_time_ms"))
        if ms is None:
            continue
        values.append(
            {
                "ms": ms,
                "status": _normalize_state(str(row.get("status") or "")),
                "checked_at": row.get("checked_at") or "",
            }
        )
    values = values[-limit:]
    max_ms = max((point["ms"] for point in values), default=1.0) or 1.0
    for point in values:
        point["height"] = max(8, min(100, int((point["ms"] / max_ms) * 100)))
        point["label"] = _format_ms(point["ms"])
    return values


async def _build_dashboard_monitors(hosts_status: list[dict[str, Any]]) -> list[dict[str, Any]]:
    since = (datetime.now(UTC) - timedelta(hours=24)).isoformat()
    monitors: list[dict[str, Any]] = []
    for item in hosts_status[:12]:
        host = item["host"]
        host_id = int(host["id"])
        services = item.get("services") or []
        primary = pick_primary_service(services)
        latest = await db.get_latest_check(int(primary["id"])) if primary else None
        rows = await db.list_host_check_history_since(host_id, since=since, limit=1200)
        rows_for_primary = [
            row
            for row in rows
            if not primary or int(row.get("service_id") or 0) == int(primary["id"])
        ]
        state = _normalize_state(item.get("overall_state"))
        uptime = uptime_percent(rows)
        avg_response = average_response_ms(rows_for_primary or rows)
        current_response = (
            _safe_ms(latest.get("response_time_ms"))
            if latest
            else None
        )
        monitors.append(
            {
                "id": host_id,
                "name": host.get("name") or host.get("address") or f"Device {host_id}",
                "address": host.get("address") or "",
                "href": f"/devices/host-{host_id}",
                "state": state,
                "display_state": _display_state(state),
                "status_class": state,
                "uptime_24h": uptime,
                "uptime_label": _format_percent(uptime),
                "avg_response_ms": avg_response,
                "avg_response_label": _format_ms(avg_response),
                "current_response_ms": current_response,
                "current_response_label": _format_ms(current_response),
                "service_count": len(services),
                "primary_service": primary.get("name") if primary else "",
                "bars": build_heartbeat_bars(rows, hours=24, buckets=26),
                "response_points": _response_points(rows_for_primary or rows),
            }
        )
    monitors.sort(
        key=lambda row: (
            {"critical": 0, "warning": 1, "unknown": 2, "ok": 3}.get(row["state"], 4),
            row["name"].lower(),
        )
    )
    return monitors


def _discovered_sort_key(device: dict[str, Any]) -> tuple[int, int, str]:
    dtype = _discovered_type(device)
    gateway_rank = 0 if dtype in {"router", "gateway"} else 1
    confidence = int(device.get("confidence") or 0)
    return (gateway_rank, -confidence, str(device.get("ip_address") or ""))


async def build_logical_topology_graph() -> dict[str, Any]:
    """Build nodes/edges for the animated logical topology (no demo data)."""
    networks = await discovery_store.list_monitored_networks(enabled_only=True)
    hosts_status = await db.get_hosts_status()
    running_checks = {int(c.get("service_id") or 0) for c in activity_service.get_running_checks()}
    monitored_cidrs = [str(n.get("cidr") or "") for n in networks if n.get("cidr")]
    inventory = await discovery_store.list_discovery_inventory(
        subnets=monitored_cidrs or None,
        limit=MAX_TOPOLOGY_DISCOVERED_PER_SUBNET * max(1, len(networks), 4) * 4,
        hide_demo=True,
    )
    inventory = [row for row in inventory if str(row.get("device_state") or "") != "ignored"]
    latest_scan = next((s for s in await db.list_discovery_scans(limit=5) if s.get("status")), None)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    gateways = [
        h
        for h in hosts_status
        if str(h["host"].get("host_type") or "").lower() in {"router", "gateway"}
    ]
    gateway = gateways[0] if gateways else None
    gateway_id = "gateway-primary"

    if gateway:
        host = gateway["host"]
        state = _normalize_state(gateway["overall_state"])
        nodes.append(
            {
                "id": gateway_id,
                "kind": "router",
                "label": _gateway_label(host),
                "ip": host.get("address") or "",
                "state": state,
                "href": f"/devices/host-{host['id']}",
                "layer": 1,
                "host_id": host["id"],
                "checking": any(s["id"] in running_checks for s in gateway["services"]),
            }
        )

    imported_ips = {
        str(item["host"].get("address") or "").strip()
        for item in hosts_status
        if item.get("host")
    }
    imported_macs = {
        str(item["host"].get("mac_address") or "").strip().lower()
        for item in hosts_status
        if item.get("host") and item["host"].get("mac_address")
    }

    subnet_keys: list[str] = []
    hosts_by_subnet: dict[str, list[dict]] = {}
    for item in hosts_status:
        host = item["host"]
        if str(host.get("host_type") or "").lower() in {"router", "gateway"} and gateway:
            if int(host["id"]) == int(gateway["host"]["id"]):
                continue
        address = str(host.get("address") or "")
        seg = str(host.get("network_segment") or "").strip() or _cidr_for_address(address, networks)
        if seg not in hosts_by_subnet:
            hosts_by_subnet[seg] = []
            subnet_keys.append(seg)
        hosts_by_subnet[seg].append(item)

    discovered = _dedupe_discovered_devices(inventory)
    discovered_by_subnet: dict[str, list[dict[str, Any]]] = {}
    for device in discovered:
        ip = str(device.get("ip_address") or "").strip()
        mac = str(device.get("mac_address") or "").strip().lower()
        if not ip or ip in imported_ips or (mac and mac in imported_macs):
            continue
        cidr = str(device.get("subnet") or "").strip() or _cidr_for_address(ip, networks)
        if cidr not in discovered_by_subnet:
            discovered_by_subnet[cidr] = []
            if cidr not in subnet_keys:
                subnet_keys.append(cidr)
        discovered_by_subnet[cidr].append(device)

    if not nodes and not hosts_by_subnet and not discovered_by_subnet:
        return {
            "nodes": [],
            "edges": [],
            "logical": True,
            "empty": True,
            "empty_reason": "no_real_devices",
            "network_count": len(networks),
            "device_count": 0,
            "discovered_count": 0,
            "active_scan_id": latest_scan.get("id") if latest_scan else None,
            "active_scan_status": latest_scan.get("status") if latest_scan else "",
            "active_scan_found": latest_scan.get("found_count") if latest_scan else 0,
        }

    for cidr in subnet_keys:
        sid = _subnet_node_id(cidr, networks)
        net = next((n for n in networks if str(n.get("cidr") or "") == cidr), None)
        nodes.append(
            {
                "id": sid,
                "kind": "subnet",
                "label": _network_label(net) if net else cidr,
                "ip": cidr if cidr != "unknown" else "",
                "vlan": (net or {}).get("vlan_id") or "",
                "state": "ok",
                "href": "/discovery?tab=networks",
                "layer": 2,
            }
        )
        if gateway:
            edges.append({"from": gateway_id, "to": sid, "state": "ok"})

    for cidr in subnet_keys:
        sid = _subnet_node_id(cidr, networks)
        bucket_hosts = hosts_by_subnet.get(cidr, [])
        shown = bucket_hosts[:MAX_TOPOLOGY_DEVICES_PER_SUBNET]
        overflow = max(0, len(bucket_hosts) - len(shown))
        for item in shown:
            host = item["host"]
            hid = int(host["id"])
            state = _normalize_state(item["overall_state"])
            checking = any(int(s["id"]) in running_checks for s in item["services"])
            node_id = f"host-{hid}"
            nodes.append(
                {
                    "id": node_id,
                    "kind": _device_type(host),
                    "label": host.get("name") or host.get("address") or f"Device {hid}",
                    "ip": host.get("address") or "",
                    "state": state,
                    "href": f"/devices/host-{hid}",
                    "layer": 3,
                    "host_id": hid,
                    "checking": checking,
                    "subnet_id": sid,
                }
            )
            edges.append({"from": sid, "to": node_id, "state": state, "animated": checking})
        if overflow:
            nodes.append(
                {
                    "id": f"{sid}-more",
                    "kind": "cluster",
                    "label": f"+{overflow}",
                    "state": "unknown",
                    "href": "/devices",
                    "layer": 3,
                    "subnet_id": sid,
                }
            )
            edges.append({"from": sid, "to": f"{sid}-more", "state": "unknown"})

    for cidr, devices in discovered_by_subnet.items():
        sid = _subnet_node_id(cidr, networks)
        ordered_devices = sorted(devices, key=_discovered_sort_key)
        shown = ordered_devices[:MAX_TOPOLOGY_DISCOVERED_PER_SUBNET]
        overflow = max(0, len(devices) - len(shown))
        for device in shown:
            did = int(device["id"])
            ip = str(device.get("ip_address") or "")
            confidence = int(device.get("confidence") or 0)
            node_id = f"discovery-inv-{did}"
            nodes.append(
                {
                    "id": node_id,
                    "kind": _discovered_type(device),
                    "label": _discovered_label(device),
                    "ip": ip,
                    "state": _inventory_state(device.get("device_state")),
                    "href": f"/discovery?tab=results&scan_id={device.get('last_scan_id')}",
                    "layer": 3,
                    "subnet_id": sid,
                    "discovered": True,
                    "confidence": confidence,
                    "source": "scan",
                }
            )
            edges.append({"from": sid, "to": node_id, "state": "unknown", "discovered": True})
        if overflow:
            node_id = f"{sid}-discovered-more"
            nodes.append(
                {
                    "id": node_id,
                    "kind": "cluster",
                    "label": f"+{overflow}",
                    "state": "unknown",
                    "href": "/discovery?tab=new",
                    "layer": 3,
                    "subnet_id": sid,
                    "discovered": True,
                }
            )
            edges.append({"from": sid, "to": node_id, "state": "unknown", "discovered": True})

    return {
        "nodes": nodes,
        "edges": edges,
        "logical": True,
        "empty": False,
        "network_count": len(networks),
        "device_count": len(hosts_status),
        "discovered_count": sum(len(items) for items in discovered_by_subnet.values()),
        "active_scan_id": latest_scan.get("id") if latest_scan else None,
        "active_scan_status": latest_scan.get("status") if latest_scan else "",
        "active_scan_found": latest_scan.get("found_count") if latest_scan else 0,
    }


async def _build_problems(
    hosts_status: list[dict],
    license_status: dict[str, Any],
    lang: str,
) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = []

    for item in hosts_status:
        state = _normalize_state(item["overall_state"])
        if state not in {"critical", "warning"}:
            continue
        host = item["host"]
        problems.append(
            {
                "severity": state,
                "title": host.get("name") or host.get("address") or "Device",
                "message": translate("dashboard.problem.device_state", lang, state=state),
                "time": host.get("updated_at") or host.get("created_at") or "",
                "href": f"/devices/host-{host['id']}",
                "kind": "device",
            }
        )

    services = await db.list_services()
    hosts = {h["id"]: h for h in await db.list_hosts()}
    alerts = await db.get_recent_alerts(limit=20)
    for alert in alerts:
        if alert.get("resolved_at"):
            continue
        sev = str(alert.get("severity") or "warning").lower()
        if sev not in {"critical", "warning"}:
            continue
        svc = next((s for s in services if int(s["id"]) == int(alert.get("service_id") or 0)), None)
        host = hosts.get(int(svc["host_id"])) if svc else None
        problems.append(
            {
                "severity": sev,
                "title": (host or {}).get("name") or translate("dashboard.problem.service", lang),
                "message": str(alert.get("message") or "")[:120],
                "time": alert.get("created_at") or "",
                "href": f"/devices/host-{host['id']}" if host else "/alerts",
                "kind": "alert",
            }
        )

    scans = await db.list_discovery_scans(limit=5)
    for scan in scans:
        if str(scan.get("status") or "").lower() == "failed":
            problems.append(
                {
                    "severity": "warning",
                    "title": translate("dashboard.problem.scan_failed", lang),
                    "message": str(scan.get("error_message") or translate("dashboard.problem.scan_failed_desc", lang)),
                    "time": scan.get("finished_at") or scan.get("created_at") or "",
                    "href": f"/discovery?tab=results&scan_id={scan['id']}",
                    "kind": "scan",
                }
            )
            break

    max_hosts = license_status.get("max_hosts")
    used_hosts = int(license_status.get("used_hosts") or 0)
    if max_hosts and used_hosts >= int(max_hosts):
        problems.append(
            {
                "severity": "warning",
                "title": translate("dashboard.problem.license_limit", lang),
                "message": translate(
                    "dashboard.problem.license_limit_desc",
                    lang,
                    used=used_hosts,
                    max=max_hosts,
                ),
                "time": "",
                "href": "/license",
                "kind": "license",
            }
        )

    problems.sort(key=lambda p: _STATE_SEVERITY.get(str(p["severity"]), 0), reverse=True)
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for p in problems:
        key = f"{p['kind']}:{p['title']}:{p['message'][:40]}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    return unique[:MAX_PROBLEMS]


def _activity_label(event: dict[str, Any], lang: str) -> str:
    event_type = str(event.get("event_type") or "").lower()
    message = str(event.get("message") or "").strip()
    mapping = {
        "scan_started": "dashboard.activity.scan_started",
        "scan_completed": "dashboard.activity.scan_completed",
        "scan_failed": "dashboard.activity.scan_failed",
        "device_discovered": "dashboard.activity.device_discovered",
        "device_missing": "dashboard.activity.device_missing",
        "service_recovered": "dashboard.activity.service_recovered",
        "alert_created": "dashboard.activity.alert_created",
        "alert_resolved": "dashboard.activity.alert_resolved",
        "check_completed": "dashboard.activity.check_completed",
    }
    for key, i18n_key in mapping.items():
        if key in event_type:
            return translate(i18n_key, lang)
    if message:
        return message[:100]
    return translate("dashboard.activity.event", lang)


async def _build_activity(lang: str) -> list[dict[str, Any]]:
    logs = await db.list_system_logs(limit=40, offset=0)
    activity: list[dict[str, Any]] = []
    for row in logs:
        category = str(row.get("category") or "")
        if category not in {"discovery", "monitoring", "system", "notification", "license"}:
            continue
        entity_id = str(row.get("entity_id") or "")
        href = ""
        if row.get("entity_type") == "host" and entity_id.isdigit():
            href = f"/devices/host-{entity_id}"
        elif category == "discovery":
            href = "/discovery"
        activity.append(
            {
                "level": str(row.get("level") or "info"),
                "label": _activity_label(row, lang),
                "time": row.get("created_at") or "",
                "href": href,
                "category": category,
            }
        )
        if len(activity) >= MAX_ACTIVITY:
            break
    return activity


async def build_dashboard_overview(lang: str = "en") -> dict[str, Any]:
    """Full dashboard payload for the overview control center."""
    stats = await db.get_enhanced_dashboard()
    license_status = await license_service.status()
    activity_summary = await activity_service.get_summary()
    hosts_status = await db.get_hosts_status()
    networks = await discovery_store.list_monitored_networks(enabled_only=True)
    meta = build_metadata(friendly_missing=True)

    online = sum(1 for h in hosts_status if _normalize_state(h["overall_state"]) == "ok")
    warning = sum(1 for h in hosts_status if _normalize_state(h["overall_state"]) == "warning")
    critical = sum(1 for h in hosts_status if _normalize_state(h["overall_state"]) == "critical")
    unknown = max(0, len(hosts_status) - online - warning - critical)
    system_status = _normalize_system_status(activity_summary.get("app_status"))

    scans = await db.list_discovery_scans(limit=10)
    last_scan = next(
        (s for s in scans if str(s.get("status") or "").lower() == "completed"),
        scans[0] if scans else None,
    )
    last_scan_time = ""
    if last_scan:
        last_scan_time = last_scan.get("finished_at") or last_scan.get("created_at") or ""

    topology = await build_logical_topology_graph()
    problems = await _build_problems(hosts_status, license_status, lang)
    activity = await _build_activity(lang)
    monitors = await _build_dashboard_monitors(hosts_status)
    selected_monitor = monitors[0] if monitors else None

    tier = str(license_status.get("tier") or "FREE").upper()
    show_corporate = bool(
        license_status.get("employee_presence_enabled")
        or license_status.get("multi_office_enabled")
    )

    max_hosts_raw = license_status.get("max_hosts")
    try:
        max_hosts = int(max_hosts_raw) if max_hosts_raw not in (None, "") else None
        if max_hosts is not None and max_hosts <= 0:
            max_hosts = None
    except (TypeError, ValueError):
        max_hosts = None
    used_hosts = int(license_status.get("used_hosts") or 0)
    license_pct = int((used_hosts / max_hosts) * 100) if max_hosts else 0
    near_limit = bool(max_hosts and license_pct >= 80)
    uptime_all = round((online / len(hosts_status)) * 100, 2) if hosts_status else None

    return {
        "header": {
            "tier": tier,
            "version": meta.get("version") or stats.get("app_version", ""),
            "networks": len(networks),
            "total_devices": int(stats.get("total_hosts") or 0),
            "online": online,
            "offline": critical,
            "warning": warning,
            "critical": critical,
            "unknown": unknown,
            "active_alerts": int(stats.get("active_alerts") or 0),
            "last_scan": last_scan_time,
            "system_status": system_status,
            "checks_running": int(activity_summary.get("checks_running") or 0),
            "uptime_all": uptime_all,
            "uptime_all_label": _format_percent(uptime_all),
        },
        "kpis": {
            "networks": len(networks),
            "total_devices": int(stats.get("total_hosts") or 0),
            "online": online,
            "offline": critical,
            "warning": warning,
            "critical": critical,
            "discovered": int(topology.get("discovered_count") or 0),
            "checks_running": int(activity_summary.get("checks_running") or 0),
            "last_discovery": last_scan_time,
            "license_usage_percent": license_pct,
            "license_hosts_used": used_hosts,
            "license_hosts_max": max_hosts,
            "license_services_used": int(license_status.get("used_services") or 0),
            "license_services_max": license_status.get("max_services"),
            "license_subnets_used": int(license_status.get("used_subnets") or 0),
            "license_subnets_max": license_status.get("max_subnets"),
        },
        "monitors": monitors,
        "selected_monitor": selected_monitor,
        "topology": topology,
        "problems": problems,
        "activity": activity,
        "license": {
            **license_status,
            "near_limit": near_limit,
            "usage_percent": license_pct,
        },
        "show_corporate_widgets": show_corporate,
        "is_free_tier": tier == "FREE",
        "stats": stats,
    }


def topology_json_for_template(topology: dict[str, Any]) -> str:
    """Safe JSON embed for dashboard template."""
    return json.dumps(topology, ensure_ascii=False).replace("</", "<\\/")
