"""Auto-import discovered devices for monitoring (no manual import step)."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from ditaknet import database as db
from ditaknet.core.licensing import LicenseLimitError, license_service
from ditaknet.discovery import store as discovery_store
from ditaknet.discovery.services import recommended_checks
from ditaknet.discovery.naming import clean_hostname, resolve_device_name_from_record
from ditaknet.profiles.device_profiles import normalize_device_type

_INFRA_SKIP_TYPES = frozenset({"mobile_phone"})


async def _employee_device_keys() -> tuple[set[str], set[str], set[str]]:
    """MAC, hostname, and IP keys for approved employee devices (presence module)."""
    try:
        conn = await db.get_db()
        rows = await conn.execute_fetchall(
            """SELECT mac_address, hostname, static_ip, last_ip
               FROM employee_devices
               WHERE is_approved = 1"""
        )
    except Exception:
        return set(), set(), set()

    macs: set[str] = set()
    hostnames: set[str] = set()
    ips: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            item = row
        else:
            item = {key: row[key] for key in row.keys()}
        mac = str(item.get("mac_address") or "").strip().lower().replace("-", ":")
        if mac:
            macs.add(mac)
        host = clean_hostname(str(item.get("hostname") or "")).lower()
        if host:
            hostnames.add(host)
        for key in ("static_ip", "last_ip"):
            ip = str(item.get(key) or "").strip()
            if ip:
                ips.add(ip)
    return macs, hostnames, ips


def _matches_employee_device(
    device: dict[str, Any],
    macs: set[str],
    hostnames: set[str],
    ips: set[str],
) -> bool:
    mac = str(device.get("mac_address") or "").strip().lower().replace("-", ":")
    if mac and mac in macs:
        return True
    ip = str(device.get("ip_address") or "").strip()
    if ip and ip in ips:
        return True
    host = clean_hostname(str(device.get("hostname") or "")).lower()
    if host and host in hostnames:
        return True
    return False


def _should_skip_device(device: dict[str, Any], *, skip_mobile: bool) -> str | None:
    device_type = normalize_device_type(str(device.get("detected_type") or "unknown"))
    if skip_mobile and device_type in _INFRA_SKIP_TYPES:
        return "mobile_phone"
    return None


async def import_discovered_device(
    device: dict[str, Any],
    *,
    create_checks: bool = True,
    scheduler: Any | None = None,
    actor: str = "auto_import",
) -> dict[str, Any]:
    """Import one discovered device row as a monitored host."""
    device_id = int(device["id"])
    if device.get("imported_host_id"):
        return {
            "device_id": device_id,
            "host_id": int(device["imported_host_id"]),
            "skipped": True,
            "reason": "already_imported",
        }

    await license_service.enforce_host_create(address=str(device.get("ip_address") or ""))

    name = resolve_device_name_from_record(device)
    host_type = normalize_device_type(str(device.get("detected_type") or "unknown"))
    host = await db.create_host(
        name=name,
        address=str(device["ip_address"]),
        host_type=host_type,
        tags="auto-discovered",
    )
    await db.update_host(
        host["id"],
        mac_address=str(device.get("mac_address") or ""),
        hostname=str(device.get("hostname") or ""),
        last_ip=str(device.get("ip_address") or ""),
        discovery_device_id=device_id,
    )
    await db.mark_discovered_device_imported(device_id, int(host["id"]))
    await discovery_store.mark_discovery_inventory_address_imported(
        ip_address=str(device.get("ip_address") or ""),
        host_id=int(host["id"]),
        scan_id=int(device.get("scan_id") or 0) or None,
    )

    services_created: list[int] = []
    if create_checks:
        ports = set(json.loads(device.get("open_ports") or "[]"))
        checks = recommended_checks(host_type, str(device["ip_address"]), ports)
        for spec in checks:
            try:
                await license_service.enforce_service_create()
            except LicenseLimitError:
                break
            svc = await db.create_service(
                host_id=host["id"],
                name=spec["name"],
                check_type=spec["check_type"],
                target=spec["target"],
                port=spec.get("port"),
                interval_seconds=spec.get("interval_seconds", 60),
                timeout_seconds=spec.get("timeout_seconds", 10),
                expected_status_code=spec.get("expected_status_code", 200),
            )
            if svc.get("enabled") and scheduler is not None and hasattr(scheduler, "add_service"):
                scheduler.add_service(svc)
            services_created.append(int(svc["id"]))

    try:
        await db.create_audit_log(
            "discovery.auto_import",
            actor=actor,
            resource="host",
            resource_id=str(host["id"]),
            detail=f"{name} ({device.get('ip_address')}) type={host_type}",
        )
    except Exception:
        pass

    return {
        "device_id": device_id,
        "host_id": int(host["id"]),
        "name": name,
        "host_type": host_type,
        "services": services_created,
    }


async def auto_import_devices(
    devices: list[dict[str, Any]],
    *,
    create_checks: bool = True,
    scheduler: Any | None = None,
    skip_mobile: bool = True,
    actor: str = "auto_import",
) -> dict[str, Any]:
    """Import a list of discovered device rows, skipping employee/mobile devices."""
    macs, hostnames, ips = await _employee_device_keys()
    imported: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for device in devices:
        if device.get("imported_host_id"):
            skipped.append({"device_id": device.get("id"), "reason": "already_imported"})
            continue
        if _matches_employee_device(device, macs, hostnames, ips):
            skipped.append(
                {
                    "device_id": device.get("id"),
                    "ip": device.get("ip_address"),
                    "reason": "employee_device",
                }
            )
            continue
        skip_reason = _should_skip_device(device, skip_mobile=skip_mobile)
        if skip_reason:
            skipped.append(
                {
                    "device_id": device.get("id"),
                    "ip": device.get("ip_address"),
                    "reason": skip_reason,
                }
            )
            continue
        try:
            result = await import_discovered_device(
                device,
                create_checks=create_checks,
                scheduler=scheduler,
                actor=actor,
            )
            if result.get("skipped"):
                skipped.append(result)
            else:
                imported.append(result)
        except LicenseLimitError as exc:
            errors.append({"reason": "license_limit", "detail": str(exc)})
            break
        except Exception as exc:
            errors.append(
                {
                    "device_id": device.get("id"),
                    "ip": device.get("ip_address"),
                    "reason": type(exc).__name__,
                    "detail": str(exc)[:200],
                }
            )

    return {
        "imported_count": len(imported),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
    }


async def auto_import_scan(
    scan_id: int,
    *,
    scheduler: Any | None = None,
    create_checks: bool = True,
) -> dict[str, Any]:
    """Auto-import all devices from a completed scan."""
    settings = await discovery_store.get_discovery_settings()
    if not settings.get("auto_import_enabled", True):
        return {"imported_count": 0, "skipped_count": 0, "disabled": True}

    devices = await db.list_discovered_devices(scan_id=scan_id, hide_demo=True)
    pending = [d for d in devices if not d.get("imported_host_id")]
    result = await auto_import_devices(
        pending,
        create_checks=create_checks,
        scheduler=scheduler,
        skip_mobile=settings.get("auto_import_skip_mobile", True),
    )
    result["scan_id"] = scan_id
    logger.info(
        "Discovery auto-import scan {}: imported={} skipped={} errors={}",
        scan_id,
        result["imported_count"],
        result["skipped_count"],
        result["error_count"],
    )
    from ditaknet.discovery.name_sync import refresh_host_names_from_discovery

    await refresh_host_names_from_discovery()
    return result


async def auto_import_all_pending(
    *,
    scheduler: Any | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Import all pending discovered devices (startup catch-up)."""
    settings = await discovery_store.get_discovery_settings()
    if not settings.get("auto_import_enabled", True):
        return {"imported_count": 0, "skipped_count": 0, "disabled": True}

    pending = await db.list_pending_discovered_inventory(limit=limit)
    if not pending:
        return {"imported_count": 0, "skipped_count": 0, "pending": 0}

    result = await auto_import_devices(
        pending,
        create_checks=True,
        scheduler=scheduler,
        skip_mobile=settings.get("auto_import_skip_mobile", True),
        actor="startup_auto_import",
    )
    result["pending"] = len(pending)
    logger.info(
        "Discovery startup auto-import: pending={} imported={} skipped={}",
        len(pending),
        result["imported_count"],
        result["skipped_count"],
    )
    from ditaknet.discovery.name_sync import refresh_host_names_from_discovery

    await refresh_host_names_from_discovery()
    return result
