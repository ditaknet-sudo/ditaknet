"""
Device profile definitions — recommended checks, thresholds, icons, and hints.

Profiles guide import, bulk apply, and the troubleshooting assistant without
executing any remote commands on devices.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DeviceProfile:
    device_type: str
    display_name: str
    icon: str
    common_ports: list[int]
    warning_threshold: int = 1
    critical_threshold: int = 3
    check_specs: list[dict[str, Any]] = field(default_factory=list)
    setup_hint_key: str = ""
    troubleshoot_hint_key: str = ""


def _checks(*specs: dict[str, Any]) -> list[dict[str, Any]]:
    return list(specs)


DEVICE_PROFILES: dict[str, DeviceProfile] = {
    "router": DeviceProfile(
        device_type="router",
        display_name="Router / Gateway",
        icon="bi-router",
        common_ports=[80, 443, 22, 23, 161],
        check_specs=_checks(
            {"name": "Ping", "check_type": "ping", "interval_seconds": 60, "timeout_seconds": 5},
            {"name": "HTTP", "check_type": "http", "port": 80, "interval_seconds": 120, "timeout_seconds": 10},
        ),
        setup_hint_key="profile.router.setup",
        troubleshoot_hint_key="profile.router.troubleshoot",
    ),
    "switch": DeviceProfile(
        device_type="switch",
        display_name="Network Switch",
        icon="bi-hdd-network",
        common_ports=[22, 23, 161, 80, 443],
        check_specs=_checks(
            {"name": "Ping", "check_type": "ping", "interval_seconds": 60, "timeout_seconds": 5},
            {"name": "SNMP", "check_type": "tcp", "port": 161, "interval_seconds": 120, "timeout_seconds": 5},
        ),
        setup_hint_key="profile.switch.setup",
        troubleshoot_hint_key="profile.switch.troubleshoot",
    ),
    "access_point": DeviceProfile(
        device_type="access_point",
        display_name="Access Point",
        icon="bi-wifi",
        common_ports=[80, 443, 22],
        check_specs=_checks(
            {"name": "Ping", "check_type": "ping", "interval_seconds": 60, "timeout_seconds": 5},
            {"name": "HTTP", "check_type": "http", "port": 80, "interval_seconds": 120, "timeout_seconds": 10},
        ),
        setup_hint_key="profile.access_point.setup",
        troubleshoot_hint_key="profile.access_point.troubleshoot",
    ),
    "camera": DeviceProfile(
        device_type="camera",
        display_name="IP Camera",
        icon="bi-camera-video",
        common_ports=[80, 443, 554, 8000, 8080],
        check_specs=_checks(
            {"name": "Ping", "check_type": "ping", "interval_seconds": 60, "timeout_seconds": 5},
            {"name": "RTSP 554", "check_type": "tcp", "port": 554, "interval_seconds": 60, "timeout_seconds": 5},
            {"name": "HTTP", "check_type": "http", "port": 80, "interval_seconds": 120, "timeout_seconds": 10},
        ),
        setup_hint_key="profile.camera.setup",
        troubleshoot_hint_key="profile.camera.troubleshoot",
    ),
    "nvr": DeviceProfile(
        device_type="nvr",
        display_name="NVR / Recorder",
        icon="bi-camera-reels",
        common_ports=[80, 443, 554, 8000],
        check_specs=_checks(
            {"name": "Ping", "check_type": "ping", "interval_seconds": 60, "timeout_seconds": 5},
            {"name": "HTTP", "check_type": "http", "port": 80, "interval_seconds": 120, "timeout_seconds": 10},
            {"name": "RTSP 554", "check_type": "tcp", "port": 554, "interval_seconds": 60, "timeout_seconds": 5},
        ),
        setup_hint_key="profile.nvr.setup",
        troubleshoot_hint_key="profile.nvr.troubleshoot",
    ),
    "dvr": DeviceProfile(
        device_type="dvr",
        display_name="DVR",
        icon="bi-camera-reels",
        common_ports=[80, 443, 554, 8000],
        check_specs=_checks(
            {"name": "Ping", "check_type": "ping", "interval_seconds": 60, "timeout_seconds": 5},
            {"name": "HTTP", "check_type": "http", "port": 80, "interval_seconds": 120, "timeout_seconds": 10},
            {"name": "RTSP 554", "check_type": "tcp", "port": 554, "interval_seconds": 60, "timeout_seconds": 5},
        ),
        setup_hint_key="profile.nvr.setup",
        troubleshoot_hint_key="profile.nvr.troubleshoot",
    ),
    "pc": DeviceProfile(
        device_type="pc",
        display_name="PC / Workstation",
        icon="bi-pc-display",
        common_ports=[445, 135, 3389, 22, 80, 443],
        check_specs=_checks(
            {"name": "Ping", "check_type": "ping", "interval_seconds": 60, "timeout_seconds": 5},
            {"name": "SMB 445", "check_type": "tcp", "port": 445, "interval_seconds": 120, "timeout_seconds": 5},
        ),
        setup_hint_key="profile.unknown.setup",
        troubleshoot_hint_key="profile.unknown.troubleshoot",
    ),
    "mac": DeviceProfile(
        device_type="mac",
        display_name="Mac",
        icon="bi-apple",
        common_ports=[22, 445, 548, 80, 443],
        check_specs=_checks(
            {"name": "Ping", "check_type": "ping", "interval_seconds": 60, "timeout_seconds": 5},
            {"name": "SSH 22", "check_type": "tcp", "port": 22, "interval_seconds": 120, "timeout_seconds": 5},
        ),
        setup_hint_key="profile.unknown.setup",
        troubleshoot_hint_key="profile.unknown.troubleshoot",
    ),
    "mobile_phone": DeviceProfile(
        device_type="mobile_phone",
        display_name="Mobile Phone",
        icon="bi-phone",
        common_ports=[],
        check_specs=_checks(
            {"name": "Ping", "check_type": "ping", "interval_seconds": 120, "timeout_seconds": 5},
        ),
        setup_hint_key="profile.unknown.setup",
        troubleshoot_hint_key="profile.unknown.troubleshoot",
    ),
    "linux_server": DeviceProfile(
        device_type="linux_server",
        display_name="Linux Server",
        icon="bi-server",
        common_ports=[22, 80, 443, 3306, 5432],
        check_specs=_checks(
            {"name": "Ping", "check_type": "ping", "interval_seconds": 60, "timeout_seconds": 5},
            {"name": "SSH 22", "check_type": "tcp", "port": 22, "interval_seconds": 60, "timeout_seconds": 5},
            {"name": "HTTP", "check_type": "http", "port": 80, "interval_seconds": 120, "timeout_seconds": 10},
        ),
        setup_hint_key="profile.linux_server.setup",
        troubleshoot_hint_key="profile.linux_server.troubleshoot",
    ),
    "windows_server": DeviceProfile(
        device_type="windows_server",
        display_name="Windows Server",
        icon="bi-windows",
        common_ports=[3389, 445, 135, 139, 80, 443],
        check_specs=_checks(
            {"name": "Ping", "check_type": "ping", "interval_seconds": 60, "timeout_seconds": 5},
            {"name": "RDP 3389", "check_type": "tcp", "port": 3389, "interval_seconds": 60, "timeout_seconds": 5},
            {"name": "SMB 445", "check_type": "tcp", "port": 445, "interval_seconds": 120, "timeout_seconds": 5},
        ),
        setup_hint_key="profile.windows_server.setup",
        troubleshoot_hint_key="profile.windows_server.troubleshoot",
    ),
    "printer": DeviceProfile(
        device_type="printer",
        display_name="Printer",
        icon="bi-printer",
        common_ports=[9100, 515, 631, 80],
        check_specs=_checks(
            {"name": "Ping", "check_type": "ping", "interval_seconds": 60, "timeout_seconds": 5},
            {"name": "JetDirect 9100", "check_type": "tcp", "port": 9100, "interval_seconds": 120, "timeout_seconds": 5},
        ),
        setup_hint_key="profile.printer.setup",
        troubleshoot_hint_key="profile.printer.troubleshoot",
    ),
    "nas": DeviceProfile(
        device_type="nas",
        display_name="NAS",
        icon="bi-device-hdd",
        common_ports=[80, 443, 445, 22],
        check_specs=_checks(
            {"name": "Ping", "check_type": "ping", "interval_seconds": 60, "timeout_seconds": 5},
            {"name": "HTTP", "check_type": "http", "port": 80, "interval_seconds": 120, "timeout_seconds": 10},
            {"name": "SMB 445", "check_type": "tcp", "port": 445, "interval_seconds": 120, "timeout_seconds": 5},
        ),
        setup_hint_key="profile.nas.setup",
        troubleshoot_hint_key="profile.nas.troubleshoot",
    ),
    "website": DeviceProfile(
        device_type="website",
        display_name="Website / Web Service",
        icon="bi-globe",
        common_ports=[80, 443, 8080, 8443],
        check_specs=_checks(
            {"name": "Ping", "check_type": "ping", "interval_seconds": 60, "timeout_seconds": 5},
            {"name": "HTTPS", "check_type": "http", "port": 443, "interval_seconds": 60, "timeout_seconds": 10, "https": True},
        ),
        setup_hint_key="profile.website.setup",
        troubleshoot_hint_key="profile.website.troubleshoot",
    ),
    "unknown": DeviceProfile(
        device_type="unknown",
        display_name="Unknown Device",
        icon="bi-question-circle",
        common_ports=[],
        check_specs=_checks(
            {"name": "Ping", "check_type": "ping", "interval_seconds": 60, "timeout_seconds": 5},
        ),
        setup_hint_key="profile.unknown.setup",
        troubleshoot_hint_key="profile.unknown.troubleshoot",
    ),
}

_ALIASES = {
    "server_linux": "linux_server",
    "server_windows": "windows_server",
    "server": "linux_server",
    "workstation": "pc",
    "desktop": "pc",
    "desktop_pc": "pc",
    "macos": "mac",
    "imac": "mac",
    "macbook": "mac",
    "phone": "mobile_phone",
    "smartphone": "mobile_phone",
    "wifi": "access_point",
    "ap": "access_point",
    "web_interface": "unknown",
}


def normalize_device_type(device_type: str) -> str:
    key = (device_type or "unknown").lower().replace("-", "_")
    return _ALIASES.get(key, key if key in DEVICE_PROFILES else "unknown")


def get_profile(device_type: str) -> DeviceProfile:
    return DEVICE_PROFILES[normalize_device_type(device_type)]


def list_profiles() -> list[dict[str, Any]]:
    return [
        {
            "device_type": p.device_type,
            "display_name": p.display_name,
            "icon": p.icon,
            "common_ports": p.common_ports,
            "warning_threshold": p.warning_threshold,
            "critical_threshold": p.critical_threshold,
            "check_count": len(p.check_specs),
            "setup_hint_key": p.setup_hint_key,
            "troubleshoot_hint_key": p.troubleshoot_hint_key,
        }
        for p in DEVICE_PROFILES.values()
    ]


def profile_detail(device_type: str, lang: str = "en") -> dict[str, Any]:
    from ditaknet.i18n import translate
    from ditaknet.profiles.recommended_checks import build_checks_for_host

    profile = get_profile(device_type)
    return {
        "device_type": profile.device_type,
        "display_name": profile.display_name,
        "icon": profile.icon,
        "common_ports": profile.common_ports,
        "warning_threshold": profile.warning_threshold,
        "critical_threshold": profile.critical_threshold,
        "recommended_checks": profile.check_specs,
        "setup_hint": translate(profile.setup_hint_key, lang) if profile.setup_hint_key else "",
        "troubleshoot_hint": translate(profile.troubleshoot_hint_key, lang) if profile.troubleshoot_hint_key else "",
        "example_checks": build_checks_for_host(profile.device_type, "192.168.1.1", set(profile.common_ports)),
    }
