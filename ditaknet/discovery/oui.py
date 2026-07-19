"""MAC OUI vendor lookup (embedded common vendors — not a full IEEE database)."""

from __future__ import annotations

# Prefix (uppercase, no separators) -> vendor name
_OUI_VENDORS: dict[str, str] = {
    "001A2B": "Cisco",
    "001B63": "Apple",
    "001C23": "Hikvision",
    "0021CC": "Hikvision",
    "0022CF": "Ubiquiti",
    "002590": "TP-Link",
    "003048": "Supermicro",
    "005056": "VMware",
    "080027": "VirtualBox",
    "0C9D92": "ASUS",
    "18E829": "Ubiquiti",
    "24A43C": "Ubiquiti",
    "28EE52": "Raspberry Pi",
    "3C52A1": "Hewlett Packard",
    "48D705": "Hewlett Packard",
    "5C260A": "Dell",
    "6C3B6B": "Netgear",
    "708BCD": "D-Link",
    "7C10C9": "ASUS",
    "B827EB": "Raspberry Pi",
    "C0C1C0": "Cisco-Linksys",
    "D850E6": "ASUS",
    "E45F01": "Raspberry Pi",
    "F832E4": "ASUS",
    "FCAA14": "Synology",
    "00000C": "Cisco",
    "001A11": "Google",
    "001A70": "Mikrotik",
    "001A79": "Huawei Technologies",
    "001A92": "Huawei",
    "001B78": "Intel Corporate",
    "001CF0": "D-Link",
    "001D0F": "TP-Link",
    "001E10": "Huawei",
    "001E64": "Intel Corporate",
    "001E8A": "TP-Link",
    "001F3B": "Intel Corporate",
    "002191": "TP-Link",
    "002454": "Samsung",
    "00269D": "Mikrotik",
    "0026BB": "Apple",
    "14CC20": "TP-Link",
    "28E31F": "Xiaomi Communications",
    "34CE00": "Xiaomi Communications",
    "3C970E": "Intel Corporate",
    "50C7BF": "TP-Link",
    "640980": "Beijing Xiaomi Mobile Software",
    "7C5CF8": "Intel Corporate",
    "8CBEBE": "Xiaomi Communications",
    "F0B429": "Xiaomi Communications",
    "F48B32": "Xiaomi Communications",
    "F8F082": "NAGTECH LLC",
    "C074AD": "Grandstream Networks, Inc.",
    "F4B19C": "AltoBeam (China) Inc.",
    "7C25DA": "FN-LINK TECHNOLOGY LIMITED",
    "CCD843": "Beijing Xiaomi Mobile Software Co., Ltd",
    "BC6EE2": "Intel Corporate",
}


def normalize_mac(mac: str | None) -> str:
    if not mac:
        return ""
    return "".join(ch for ch in mac.upper() if ch in "0123456789ABCDEF")


def lookup_vendor(mac: str | None) -> str:
    """Return vendor name from MAC OUI or empty string."""
    normalized = normalize_mac(mac)
    if len(normalized) < 6:
        return ""
    prefix = normalized[:6]
    return _OUI_VENDORS.get(prefix, "")
