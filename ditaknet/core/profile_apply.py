"""Apply device profiles to hosts and create recommended checks."""

from __future__ import annotations

from typing import Any

from ditaknet import database as db
from ditaknet.api.deps import get_scheduler
from ditaknet.core.licensing import LicenseLimitError, license_service
from ditaknet.profiles.device_profiles import normalize_device_type
from ditaknet.profiles.recommended_checks import build_checks_for_host


async def apply_profile_to_host(
    host_id: int,
    device_type: str,
    *,
    open_ports: set[int] | None = None,
) -> dict[str, Any]:
    """Update host type and create the recommended monitoring checks."""
    host = await db.get_host(host_id)
    if not host:
        raise ValueError("Host not found")

    normalized = normalize_device_type(device_type)
    await db.update_host(host_id, host_type=normalized)

    checks = build_checks_for_host(normalized, host["address"], open_ports)
    created: list[int] = []
    scheduler = get_scheduler()

    for spec in checks:
        try:
            await license_service.enforce_service_create()
        except LicenseLimitError:
            break
        svc = await db.create_service(
            host_id=host_id,
            name=spec["name"],
            check_type=spec["check_type"],
            target=spec["target"],
            port=spec.get("port"),
            interval_seconds=spec.get("interval_seconds", 60),
            timeout_seconds=spec.get("timeout_seconds", 10),
            expected_status_code=spec.get("expected_status_code", 200),
        )
        created.append(svc["id"])
        if svc.get("enabled") and hasattr(scheduler, "add_service"):
            scheduler.add_service(svc)

    return {"host_id": host_id, "device_type": normalized, "services_created": created}
