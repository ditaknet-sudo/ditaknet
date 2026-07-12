"""Scheduled discovery refresh for monitored subnets."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from ditaknet import database as db
from ditaknet.config import settings
from ditaknet.core.licensing import license_service
from ditaknet.discovery.scheduler import discovery_scheduler
from ditaknet.discovery.subnet import normalize_subnets

REFRESH_INTERVAL_SETTING = "discovery_refresh_interval_minutes"


async def refresh_interval_minutes() -> int:
    stored = await db.get_app_setting(REFRESH_INTERVAL_SETTING)
    if stored:
        try:
            return max(1, int(stored))
        except ValueError:
            pass
    return max(1, int(settings.discovery_refresh_interval_minutes))


async def ensure_refresh_defaults() -> None:
    """Do not auto-register subnets; admin must configure monitored networks."""
    return


async def register_monitored_subnet(subnet: str) -> None:
    from ditaknet.discovery import store as discovery_store

    normalized = normalize_subnets([subnet.strip()])
    if not normalized:
        return
    await discovery_store.register_discovery_subnet(normalized[0])


async def run_discovery_refresh() -> dict[str, Any]:
    """Start a refresh scan when monitored networks are configured."""
    from ditaknet.discovery import store as discovery_store
    from ditaknet.discovery.networks_service import scannable_monitored_networks

    nets = await scannable_monitored_networks()
    subnets = [
        str(n.get("cidr") or "")
        for n in nets
        if n.get("cidr") and n.get("auto_refresh_enabled")
    ]
    if not subnets:
        subnets = await discovery_store.list_discovery_monitored_subnets()
    if not subnets:
        return {"started": False, "reason": "no_subnets"}

    scans = await db.list_discovery_scans(limit=5)
    if any(scan.get("status") in {"pending", "running"} for scan in scans):
        return {"started": False, "reason": "scan_already_running"}

    try:
        await license_service.enforce_discovery_scan(subnets)
    except Exception as exc:
        logger.debug("Discovery refresh skipped: {}", exc)
        return {"started": False, "reason": type(exc).__name__}

    profile = str(settings.discovery_refresh_scan_mode or "quick").lower()
    if profile not in {"quick", "normal", "deep"}:
        profile = "quick"

    scan = await db.create_discovery_scan(profile, json.dumps(subnets))
    scan_id = int(scan["id"])
    await discovery_scheduler.start_scan(scan_id, subnets, profile)
    await discovery_store.set_last_discovery_refresh_at()

    try:
        from ditaknet.core.system_log_service import record

        await record(
            "info",
            "discovery",
            "scan_started",
            f"Scheduled discovery refresh started for {subnets[0]}",
            source="discovery_refresh",
            entity_type="scan",
            entity_id=scan_id,
        )
    except Exception:
        pass

    return {"started": True, "scan_id": scan_id, "subnets": subnets}
