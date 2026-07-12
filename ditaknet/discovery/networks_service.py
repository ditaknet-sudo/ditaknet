"""Monitored network validation and license enforcement."""

from __future__ import annotations

from typing import Any

from ditaknet.core.licensing import LicenseLimitError, license_service
from ditaknet.discovery import store as discovery_store
from ditaknet.discovery.subnet import is_private_subnet, normalize_subnets


async def validate_network_payload(
    *,
    cidr: str,
    scan_mode: str = "normal",
    allow_cgnat: bool = False,
) -> str:
    normalized = normalize_subnets([cidr])
    if not normalized:
        raise ValueError("CIDR subnet is required")
    net = normalized[0]
    if not is_private_subnet(net):
        raise ValueError("Only private/local subnets may be monitored")
    if net.startswith("100.64.") and not allow_cgnat:
        raise ValueError(
            "CGNAT range 100.64.0.0/10 requires advanced confirmation; use a standard private subnet"
        )
    if scan_mode not in {"quick", "normal", "deep"}:
        raise ValueError("Scan mode must be quick, normal, or deep")
    return net


async def enforce_network_create(cidr: str) -> None:
    await validate_network_payload(cidr=cidr)
    limits = await license_service.get_limits()
    count = await discovery_store.count_monitored_networks()
    if limits.max_discovery_subnets is not None and count >= limits.max_discovery_subnets:
        raise LicenseLimitError(
            f"Your {limits.tier} package supports up to {limits.max_discovery_subnets} monitored subnet(s). "
            "Upgrade to Medium or Professional for multiple VLANs.",
            error_key="error.package.multi_vlan",
        )
    await license_service.enforce_discovery_scan([cidr])


async def create_monitored_network(**payload: Any) -> dict:
    cidr = await validate_network_payload(
        cidr=str(payload.get("cidr") or ""),
        scan_mode=str(payload.get("scan_mode") or "normal"),
    )
    await enforce_network_create(cidr)
    return await discovery_store.create_monitored_network(
        name=str(payload.get("name") or cidr),
        cidr=cidr,
        vlan_id=str(payload.get("vlan_id") or ""),
        description=str(payload.get("description") or ""),
        scan_mode=str(payload.get("scan_mode") or "normal"),
        enabled=bool(payload.get("enabled", True)),
        auto_refresh_enabled=bool(payload.get("auto_refresh_enabled", True)),
    )


async def annotate_network_limits(networks: list[dict]) -> list[dict]:
    limits = await license_service.get_limits()
    max_n = limits.max_discovery_subnets
    ordered = sorted(networks, key=lambda n: int(n.get("id") or 0))
    for idx, net in enumerate(ordered):
        over = max_n is not None and idx >= max_n
        net["over_limit"] = over
        if over:
            net["over_limit_reason"] = "license_limit_exceeded"
    return ordered


async def scannable_monitored_networks() -> list[dict]:
    nets = await discovery_store.list_monitored_networks(enabled_only=True)
    annotated = await annotate_network_limits(nets)
    return [n for n in annotated if not n.get("over_limit")]


async def update_monitored_network(network_id: int, **payload: Any) -> dict:
    existing = await discovery_store.get_monitored_network(network_id)
    if not existing:
        raise ValueError("Monitored network not found")
    cidr = str(payload.get("cidr") or existing.get("cidr") or "")
    cidr = await validate_network_payload(
        cidr=cidr,
        scan_mode=str(payload.get("scan_mode") or existing.get("scan_mode") or "normal"),
    )
    if cidr != str(existing.get("cidr") or ""):
        await license_service.enforce_discovery_scan([cidr])
    updated = await discovery_store.update_monitored_network(network_id, **payload)
    return updated or existing


async def start_network_scan(network_id: int, *, request_id: str = "") -> dict:
    net = await discovery_store.get_monitored_network(network_id)
    if not net:
        raise ValueError("Monitored network not found")
    if not net.get("enabled"):
        raise ValueError("Enable this network before scanning")
    annotated = await annotate_network_limits([net])
    if annotated and annotated[0].get("over_limit"):
        raise LicenseLimitError(
            "This subnet exceeds your license limit. Upgrade your package or remove extra subnets.",
            error_key="error.package.multi_vlan",
        )
    cidr = str(net.get("cidr") or "")
    scan_mode = str(net.get("scan_mode") or "normal")
    await license_service.enforce_discovery_scan([cidr])
    import json

    from ditaknet import database as db
    from ditaknet.discovery.scheduler import discovery_scheduler

    scan = await db.create_discovery_scan(scan_mode, json.dumps([cidr]), request_id=request_id)
    scan_id = int(scan["id"])
    await db.update_discovery_scan(scan_id, monitored_network_id=network_id)
    await discovery_scheduler.start_scan(scan_id, [cidr], scan_mode)
    await discovery_store.set_monitored_network_last_scan(network_id, scan_id)
    return {"scan_id": scan_id, "network_id": network_id, "cidr": cidr}
