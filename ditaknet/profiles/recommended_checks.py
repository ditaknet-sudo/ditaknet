"""Build concrete check rows from a device profile and host address."""

from __future__ import annotations

from typing import Any

from ditaknet.profiles.device_profiles import get_profile, normalize_device_type


def build_checks_for_host(
    device_type: str,
    address: str,
    open_ports: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Materialize profile check specs with targets filled in for *address*."""
    profile = get_profile(device_type)
    ports = open_ports or set(profile.common_ports)
    checks: list[dict[str, Any]] = []

    for spec in profile.check_specs:
        ctype = spec["check_type"]
        row: dict[str, Any] = {
            "name": spec["name"],
            "check_type": ctype,
            "interval_seconds": spec.get("interval_seconds", 60),
            "timeout_seconds": spec.get("timeout_seconds", 10),
        }
        if ctype == "ping":
            row["target"] = address
        elif ctype == "tcp":
            port = spec.get("port")
            if port and (not open_ports or port in ports):
                row["target"] = address
                row["port"] = port
            elif port:
                row["target"] = address
                row["port"] = port
            else:
                continue
        elif ctype == "http":
            port = spec.get("port", 80)
            use_https = spec.get("https") or port in (443, 8443)
            if open_ports and port not in ports and (443 not in ports if use_https else 80 not in ports):
                if not open_ports:
                    pass
                elif use_https and 443 not in ports:
                    continue
                elif not use_https and 80 not in ports and port not in ports:
                    continue
            scheme = "https" if use_https else "http"
            row["target"] = f"{scheme}://{address}:{port}"
            row["expected_status_code"] = 200
        else:
            row["target"] = address
        checks.append(row)
    return checks


def recommended_checks(device_type: str, ip_address: str, open_ports: set[int]) -> list[dict[str, Any]]:
    """Compatibility wrapper used by discovery import."""
    return build_checks_for_host(normalize_device_type(device_type), ip_address, open_ports)
