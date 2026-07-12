"""Port lists per scan profile — conservative defaults to avoid network overload."""

from __future__ import annotations

from typing import Literal

ScanProfile = Literal["quick", "normal", "deep"]

# Grouped for classifier hints; union defines scan targets per profile.
ROUTER_PORTS = {22, 23, 53, 80, 443, 161}
SERVER_PORTS = {22, 80, 443, 3389, 5900, 3306, 5432, 6379}
WINDOWS_PORTS = {135, 139, 445, 3389}
PRINTER_PORTS = {9100, 515, 631}
CAMERA_PORTS = {80, 443, 554, 8000, 8080, 8899}
NAS_PORTS = {22, 80, 443, 445, 5000, 5001}
WEB_PORTS = {80, 443, 8080, 8443}

REQUIRED_COMMON_PORTS = {
    22,
    23,
    53,
    80,
    135,
    139,
    443,
    445,
    554,
    631,
    3389,
    5000,
    5001,
    5833,
    8000,
    8080,
    8443,
    9100,
}

PROFILE_PORTS: dict[ScanProfile, set[int]] = {
    "quick": REQUIRED_COMMON_PORTS,
    "normal": (
        ROUTER_PORTS
        | SERVER_PORTS
        | WINDOWS_PORTS
        | PRINTER_PORTS
        | CAMERA_PORTS
        | NAS_PORTS
        | WEB_PORTS
        | REQUIRED_COMMON_PORTS
    ),
    "deep": (
        ROUTER_PORTS
        | SERVER_PORTS
        | WINDOWS_PORTS
        | PRINTER_PORTS
        | CAMERA_PORTS
        | NAS_PORTS
        | WEB_PORTS
        | {21, 25, 53, 110, 143, 993, 995, 1883, 8443, 9000}
    ),
}


def ports_for_profile(profile: ScanProfile) -> list[int]:
    return sorted(PROFILE_PORTS.get(profile, PROFILE_PORTS["normal"]))
