"""Resolve friendly device names from discovery metadata."""

from __future__ import annotations

import re
from typing import Any

from ditaknet.profiles.device_profiles import get_profile, normalize_device_type

_HOSTNAME_SUFFIX_RE = re.compile(r"\.(local|lan|home|internal)$", re.I)
_IP_LIKE_NAME_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_GENERIC_NAME_RE = re.compile(
    r"^(unknown device|device-\d{1,3}(?:\.\d{1,3}){3}|.+\s+\d{1,3}(?:\.\d{1,3}){3})$",
    re.I,
)


def clean_hostname(value: str) -> str:
    host = str(value or "").strip().rstrip(".")
    if not host:
        return ""
    first = host.split(".")[0].strip()
    return _HOSTNAME_SUFFIX_RE.sub("", first).strip() or first


def is_unnamed_label(name: str, address: str = "") -> bool:
    label = str(name or "").strip()
    addr = str(address or "").strip()
    if not label:
        return True
    if addr and label == addr:
        return True
    if _IP_LIKE_NAME_RE.match(label):
        return True
    if label.lower().startswith("device-"):
        return True
    if _GENERIC_NAME_RE.match(label):
        return True
    lowered = label.lower()
    if addr and lowered.endswith(addr) and any(
        token in lowered for token in ("unknown", "device", "router", "camera", "switch", "pc", "mac")
    ):
        return True
    return False


def resolve_device_name(
    *,
    hostname: str = "",
    vendor: str = "",
    detected_type: str = "unknown",
    ip_address: str = "",
    fallback_name: str = "",
) -> str:
    """Best-effort display name: hostname → vendor + type → IP label."""
    host = clean_hostname(hostname)
    if host and not _IP_LIKE_NAME_RE.match(host):
        return host[:255]

    vendor_clean = str(vendor or "").strip()
    device_type = normalize_device_type(str(detected_type or "unknown"))
    profile = get_profile(device_type)
    ip = str(ip_address or "").strip()

    if vendor_clean and device_type != "unknown":
        return f"{vendor_clean} {profile.display_name}"[:255]
    if vendor_clean:
        return vendor_clean[:255]

    fallback = str(fallback_name or "").strip()
    if fallback and not is_unnamed_label(fallback, ip):
        return fallback[:255]

    if ip:
        return f"{profile.display_name} {ip}"[:255]
    return profile.display_name[:255]


def resolve_device_name_from_record(device: dict[str, Any]) -> str:
    return resolve_device_name(
        hostname=str(device.get("hostname") or ""),
        vendor=str(device.get("vendor") or ""),
        detected_type=str(device.get("detected_type") or device.get("device_type") or "unknown"),
        ip_address=str(device.get("ip_address") or device.get("address") or ""),
        fallback_name=str(device.get("name") or ""),
    )


def enrich_inventory_device(device: dict[str, Any]) -> dict[str, Any]:
    """Attach display_name / is_unnamed flags to an inventory row."""
    enriched = dict(device)
    display_name = resolve_device_name(
        hostname=str(enriched.get("hostname") or ""),
        vendor=str(enriched.get("vendor") or ""),
        detected_type=str(enriched.get("device_type") or "unknown"),
        ip_address=str(enriched.get("address") or ""),
        fallback_name=str(enriched.get("name") or ""),
    )
    enriched["display_name"] = display_name
    enriched["is_unnamed"] = is_unnamed_label(display_name, str(enriched.get("address") or ""))
    return enriched
