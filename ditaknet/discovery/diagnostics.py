"""Discovery scan diagnostics and public result payload helpers."""

from __future__ import annotations

import ipaddress
import json
from pathlib import Path
from typing import Any

from ditaknet.discovery.scan_state import merge_progress, parse_subnets


def running_in_container() -> bool:
    if Path("/.dockerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False
    return "docker" in cgroup or "containerd" in cgroup or "kubepods" in cgroup


def gateway_for_subnet(cidr: str) -> str:
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return ""
    if network.version != 4:
        return ""
    hosts = network.hosts()
    try:
        return str(next(hosts))
    except StopIteration:
        return ""


def parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def parse_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def diagnostic(
    code: str,
    title: str,
    detail: str,
    *,
    severity: str = "info",
) -> dict[str, str]:
    return {"code": code, "title": title, "detail": detail, "severity": severity}


def build_scan_diagnostics(scan: dict[str, Any], progress: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """Return actionable diagnostics from persisted scan state."""
    progress = progress or {}
    diagnostics = [
        item
        for item in parse_json_list(scan.get("diagnostics_json"))
        if isinstance(item, dict) and item.get("code")
    ]

    status = str(scan.get("status") or "")
    subnets = parse_subnets(scan)
    scanned = int(progress.get("scanned") or scan.get("scanned_hosts") or 0)
    total = int(progress.get("total") or scan.get("total_hosts") or 0)
    found = int(progress.get("found") or scan.get("found_count") or 0)
    failed = int(progress.get("failed_probes") or scan.get("failed_probe_count") or 0)
    meta = parse_json_dict(scan.get("diagnostic_meta_json"))

    if status == "failed":
        diagnostics.append(
            diagnostic(
                "backend_scan_worker_failed",
                "Backend scan worker failed",
                str(scan.get("error_message") or "The scan worker stopped before completing."),
                severity="error",
            )
        )
    elif status == "cancelled":
        diagnostics.append(
            diagnostic(
                "scan_cancelled",
                "Scan was cancelled",
                "The scan was stopped before all targets were tested.",
                severity="warning",
            )
        )
    elif status == "completed" and found == 0:
        diagnostics.append(
            diagnostic(
                "zero_devices_found",
                "No reachable devices were found",
                "DitakNet completed the scan but did not receive ICMP or TCP responses from target hosts.",
                severity="warning",
            )
        )
        if scanned < total:
            diagnostics.append(
                diagnostic(
                    "scanner_incomplete",
                    "Scanner did not test every target",
                    f"Tested {scanned} of {total} targets. The scan may have timed out or stopped early.",
                    severity="warning",
                )
            )
        gateway_checked = bool(meta.get("gateway_checked") or scan.get("gateway_checked"))
        gateway_reachable = meta.get("gateway_reachable")
        if gateway_checked and gateway_reachable is False:
            gateway_ip = str(meta.get("gateway_ip") or "")
            diagnostics.append(
                diagnostic(
                    "gateway_not_reachable",
                    "Gateway was not reachable",
                    f"The expected gateway {gateway_ip or 'for the subnet'} did not respond to ICMP or TCP probes.",
                    severity="warning",
                )
            )
        if not gateway_checked and subnets:
            diagnostics.append(
                diagnostic(
                    "gateway_not_checked",
                    "Gateway reachability was not confirmed",
                    "The scan did not confirm a reachable gateway for the selected subnet.",
                    severity="info",
                )
            )
        diagnostics.append(
            diagnostic(
                "wrong_subnet_or_firewall",
                "Wrong subnet or firewall may be blocking probes",
                "Verify the selected subnet, device power state, host firewalls, and router/client isolation settings.",
                severity="info",
            )
        )

    permission_errors = parse_json_list(scan.get("permission_errors_json")) or meta.get("permission_errors") or []
    if permission_errors:
        diagnostics.append(
            diagnostic(
                "icmp_permission_error",
                "ICMP/ping permission issue detected",
                "Ping reported a permission problem. DitakNet still attempted safe TCP probes.",
                severity="warning",
            )
        )

    if bool(meta.get("container_limited") or scan.get("container_limited") or running_in_container()):
        diagnostics.append(
            diagnostic(
                "docker_network_limit",
                "Docker or TrueNAS networking can limit LAN discovery",
                "Bridge networking may hide ARP/MAC data or block LAN reachability. Use host networking where appropriate.",
                severity="info",
            )
        )

    if failed and status in {"completed", "failed"}:
        diagnostics.append(
            diagnostic(
                "failed_probe_count",
                "Some probes did not receive responses",
                f"{failed} probe attempt(s) did not return a reachable response.",
                severity="info",
            )
        )

    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for item in diagnostics:
        code = str(item.get("code") or "")
        if code in seen:
            continue
        seen.add(code)
        unique.append(item)
    return unique


def scan_result_payload(
    scan: dict[str, Any],
    *,
    devices: list[dict[str, Any]],
    live_progress: dict[str, Any] | None = None,
) -> dict[str, Any]:
    progress = merge_progress(scan, live_progress)
    subnets = parse_subnets(scan)
    diagnostics = build_scan_diagnostics(scan, progress)
    status = str(scan.get("status") or "")
    payload: dict[str, Any] = {
        "status": status,
        "subnet": subnets[0] if subnets else "",
        "subnets": subnets,
        "scanned": int(progress.get("scanned") or 0),
        "total": int(progress.get("total") or 0),
        "found": int(progress.get("found") or 0),
        "failed_probes": int(progress.get("failed_probes") or scan.get("failed_probe_count") or 0),
        "current_ip": progress.get("current_ip") or "",
        "current_subnet": progress.get("current_subnet") or scan.get("current_subnet") or "",
        "stage": progress.get("stage") or "",
        "stage_message": progress.get("stage_message") or "",
        "elapsed_seconds": int(progress.get("elapsed_seconds") or scan.get("elapsed_seconds") or 0),
        "probe_methods": parse_json_list(scan.get("probe_methods_json")),
        "diagnostics": diagnostics,
        "devices": devices,
        "scan_id": scan.get("id"),
        "request_id": scan.get("request_id") or "",
        "started_at": scan.get("started_at") or scan.get("created_at"),
        "finished_at": scan.get("finished_at"),
    }
    if status == "failed":
        payload["error"] = scan.get("error_message") or "Discovery scan failed"
    return payload

