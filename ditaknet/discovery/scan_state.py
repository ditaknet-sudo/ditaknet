"""Discovery scan progress helpers shared by API and web routes."""

from __future__ import annotations

import json
from typing import Any

ACTIVE_SCAN_STAGES: tuple[str, ...] = (
    "preparing",
    "validating_subnet",
    "scanning_hosts",
    "checking_ports",
    "classifying_devices",
    "saving_results",
)


def parse_subnets(scan: dict[str, Any]) -> list[str]:
    raw = scan.get("subnets_json") or scan.get("subnets") or "[]"
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item).strip()]
    except Exception:
        pass
    return []


def merge_progress(scan: dict[str, Any], live: dict[str, Any] | None) -> dict[str, Any]:
    live = live or {}
    percent = int(live.get("percent") or scan.get("progress_percent") or 0)
    scanned = int(live.get("scanned") or scan.get("scanned_hosts") or 0)
    total = int(live.get("total") or scan.get("total_hosts") or 0)
    found = int(live.get("found") or scan.get("found_count") or 0)
    failed_probes = int(live.get("failed_probes") or scan.get("failed_probe_count") or 0)
    stage = live.get("stage") or scan.get("current_stage") or ""
    elapsed_seconds = int(live.get("elapsed_seconds") or scan.get("elapsed_seconds") or 0)
    return {
        "percent": percent,
        "progress_percent": percent,
        "scanned": scanned,
        "total": total,
        "found": found,
        "failed_probes": failed_probes,
        "current_ip": live.get("current_ip") or scan.get("current_ip") or "",
        "current_subnet": live.get("current_subnet") or scan.get("current_subnet") or "",
        "stage": stage,
        "current_stage": stage,
        "stage_message": live.get("stage_message") or scan.get("stage_message") or "",
        "elapsed_seconds": elapsed_seconds,
        "cancelled": bool(live.get("cancelled")),
    }


def scan_summary(scan: dict[str, Any], progress: dict[str, Any] | None) -> dict[str, Any]:
    progress = progress or {}
    subnets = parse_subnets(scan)
    return {
        "id": scan.get("id"),
        "status": scan.get("status"),
        "profile": scan.get("profile"),
        "subnets": subnets,
        "subnet": subnets[0] if subnets else "",
        "percent": progress.get("percent", 0),
        "progress_percent": progress.get("percent", 0),
        "scanned_hosts": progress.get("scanned", 0),
        "scanned": progress.get("scanned", 0),
        "total_hosts": progress.get("total", 0),
        "total": progress.get("total", 0),
        "found_count": progress.get("found", 0),
        "found": progress.get("found", 0),
        "failed_probe_count": progress.get("failed_probes", 0),
        "failed_probes": progress.get("failed_probes", 0),
        "current_ip": progress.get("current_ip") or scan.get("current_ip") or "",
        "current_subnet": progress.get("current_subnet") or scan.get("current_subnet") or "",
        "current_stage": progress.get("stage") or scan.get("current_stage") or "",
        "stage": progress.get("stage") or scan.get("current_stage") or "",
        "stage_message": progress.get("stage_message") or scan.get("stage_message") or "",
        "elapsed_seconds": progress.get("elapsed_seconds") or scan.get("elapsed_seconds") or 0,
        "error_message": scan.get("error_message") or "",
        "request_id": scan.get("request_id") or "",
        "diagnostics_json": scan.get("diagnostics_json") or "[]",
        "diagnostic_meta_json": scan.get("diagnostic_meta_json") or "{}",
        "probe_methods_json": scan.get("probe_methods_json") or "[]",
        "started_at": scan.get("started_at"),
        "finished_at": scan.get("finished_at"),
        "created_at": scan.get("created_at"),
    }
