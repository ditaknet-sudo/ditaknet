"""Group Connected Devices rows into category folders."""

from __future__ import annotations

import re
from typing import Any

from ditaknet.discovery.naming import enrich_inventory_device
from ditaknet.profiles.device_profiles import get_profile, normalize_device_type

GROUP_ORDER: tuple[str, ...] = (
    "router",
    "switch",
    "access_point",
    "camera",
    "nvr",
    "dvr",
    "nas",
    "pc",
    "mac",
    "linux_server",
    "windows_server",
    "printer",
    "website",
    "mobile_phone",
    "agent",
)

_TYPE_LABELS = {
    "router": "Routers",
    "switch": "Switches",
    "access_point": "WiFi / Access Points",
    "camera": "Cameras",
    "nvr": "NVR / Recorders",
    "dvr": "DVR",
    "nas": "NAS",
    "pc": "PC / Workstations",
    "mac": "Mac",
    "linux_server": "Linux Servers",
    "windows_server": "Windows Servers",
    "printer": "Printers",
    "website": "Websites",
    "mobile_phone": "Mobile Phones",
    "agent": "Agents",
}

_TRAILING_IP_RE = re.compile(r"\s+\d{1,3}(?:\.\d{1,3}){3}$")

_FOLDER_PLURALS = {
    "Unknown Device": "Unknown Devices",
    "Router / Gateway": "Routers / Gateways",
    "IP Camera": "IP Cameras",
    "Network Switch": "Network Switches",
    "Access Point": "Access Points",
    "NVR / Recorder": "NVR / Recorders",
    "PC / Workstation": "PC / Workstations",
}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-") or "device"


def dedupe_inventory_by_address(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one inventory row per IP (drops duplicate auto-import rows)."""
    by_address: dict[str, dict[str, Any]] = {}
    extras: list[dict[str, Any]] = []

    for device in devices:
        address = str(device.get("address") or "").strip()
        if not address:
            extras.append(device)
            continue
        current = by_address.get(address)
        if not current:
            by_address[address] = device
            continue
        current_score = (
            int(current.get("services_total") or 0),
            1 if current.get("source") == "host" else 0,
            -int(current.get("id") or 0),
        )
        candidate_score = (
            int(device.get("services_total") or 0),
            1 if device.get("source") == "host" else 0,
            -int(device.get("id") or 0),
        )
        if candidate_score > current_score:
            by_address[address] = device

    return list(by_address.values()) + extras


def _generic_base_label(device: dict[str, Any]) -> str:
    """Strip trailing IP so generic labels group together."""
    display = str(device.get("display_name") or device.get("name") or "").strip()
    address = str(device.get("address") or "").strip()
    base = _TRAILING_IP_RE.sub("", display).strip()
    if base and base != address:
        return base
    dtype = normalize_device_type(str(device.get("device_type") or "unknown"))
    return get_profile(dtype).display_name


def _uses_name_folder(device: dict[str, Any]) -> bool:
    if device.get("is_unnamed"):
        return True
    dtype = normalize_device_type(str(device.get("device_type") or "unknown"))
    return dtype == "unknown"


def _group_key(device: dict[str, Any]) -> str:
    if str(device.get("source") or "") == "agent":
        return "agent"
    if _uses_name_folder(device):
        base = _generic_base_label(device)
        return f"name:{_slug(base)}"
    return normalize_device_type(str(device.get("device_type") or "unknown"))


def _folder_label(base: str, count: int) -> str:
    if count > 1 and base in _FOLDER_PLURALS:
        return _FOLDER_PLURALS[base]
    if count > 1 and not base.endswith("s"):
        return f"{base}s"
    return base


def _group_label(group_key: str, items: list[dict[str, Any]]) -> str:
    if group_key.startswith("name:") and items:
        base = _generic_base_label(items[0])
        return _folder_label(base, len(items))
    return _TYPE_LABELS.get(group_key, get_profile(group_key).display_name)


def _group_icon(group_key: str, items: list[dict[str, Any]]) -> str:
    if group_key.startswith("name:") and items:
        return get_profile(str(items[0].get("device_type") or "unknown")).icon
    if group_key == "agent":
        return "bi-cpu"
    return get_profile(group_key).icon


def _sort_key(group_key: str, items: list[dict[str, Any]]) -> tuple[Any, ...]:
    if group_key.startswith("name:"):
        label = _group_label(group_key, items).lower()
        return (1, label, group_key)
    try:
        return (0, GROUP_ORDER.index(group_key), group_key)
    except ValueError:
        return (0, len(GROUP_ORDER), group_key)


def group_inventory_devices(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build folder sections for the Connected Devices page."""
    unique_devices = dedupe_inventory_by_address(devices)
    enriched = [enrich_inventory_device(device) for device in unique_devices]
    buckets: dict[str, list[dict[str, Any]]] = {}
    for device in enriched:
        key = _group_key(device)
        buckets.setdefault(key, []).append(device)

    groups: list[dict[str, Any]] = []
    for group_key, items in buckets.items():
        ordered = sorted(
            items,
            key=lambda row: str(row.get("address") or row.get("display_name") or "").lower(),
        )
        groups.append(
            {
                "group_key": group_key,
                "group_id": _slug(group_key),
                "group_label": _group_label(group_key, ordered),
                "group_icon": _group_icon(group_key, ordered),
                "count": len(ordered),
                "is_folder": True,
                "is_name_folder": group_key.startswith("name:"),
                "devices": ordered,
            }
        )

    groups.sort(key=lambda group: _sort_key(group["group_key"], group["devices"]))
    return groups
