"""Device type classification from ports, vendor, and HTTP metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ditaknet.discovery.oui import lookup_vendor
from ditaknet.discovery.ports import (
    CAMERA_PORTS,
    NAS_PORTS,
    PRINTER_PORTS,
    ROUTER_PORTS,
    SERVER_PORTS,
    WEB_PORTS,
    WINDOWS_PORTS,
)

DEVICE_TYPES = (
    "router",
    "switch",
    "access_point",
    "camera",
    "nvr",
    "dvr",
    "pc",
    "mac",
    "mobile_phone",
    "linux_server",
    "windows_server",
    "printer",
    "nas",
    "web_interface",
    "website",
    "unknown",
)


@dataclass
class ClassificationResult:
    detected_type: str = "unknown"
    confidence: int = 0
    signals: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


def _evidence_for_port(port: int) -> str:
    return f"port {port} open"


def classify_device(
    *,
    open_ports: set[int],
    vendor: str = "",
    mac_address: str = "",
    http_server: str = "",
    http_title: str = "",
    hostname: str = "",
    mdns_name: str = "",
    ssdp_type: str = "",
    snmp_descr: str = "",
    onvif_hint: bool = False,
    is_gateway: bool = False,
) -> ClassificationResult:
    """Estimate device type with confidence 0–100 using evidence only."""
    vendor = vendor or lookup_vendor(mac_address)
    host_label = (hostname or mdns_name or "").lower()
    scores: dict[str, int] = {t: 0 for t in DEVICE_TYPES}
    signals: list[str] = []
    evidence: list[str] = []

    if is_gateway:
        scores["router"] += 40
        signals.append("gateway_ip")
        evidence.append("gateway IP detected")

    camera_signature_ports = CAMERA_PORTS - {80, 443}
    if open_ports & camera_signature_ports:
        scores["camera"] += 40
        signals.append("camera_ports")
        for port in sorted(open_ports & camera_signature_ports):
            evidence.append(_evidence_for_port(port))
    if 554 in open_ports:
        scores["camera"] += 20
        scores["nvr"] += 15
        scores["dvr"] += 15
        signals.append("rtsp_ports")
        evidence.append(_evidence_for_port(554))
    if onvif_hint:
        scores["camera"] += 25
        signals.append("onvif")
        evidence.append("ONVIF port pattern detected")

    if open_ports & PRINTER_PORTS:
        scores["printer"] += 50
        signals.append("printer_ports")
        for port in sorted(open_ports & PRINTER_PORTS):
            evidence.append(_evidence_for_port(port))

    has_rdp = 3389 in open_ports
    has_smb = bool(open_ports & {445, 139})
    if has_rdp:
        scores["windows_server"] += 40
        signals.append("rdp")
        evidence.append(_evidence_for_port(3389))
    if has_smb:
        scores["windows_server"] += 15
        evidence.append(_evidence_for_port(445 if 445 in open_ports else 139))

    has_ssh = 22 in open_ports
    if has_ssh and not (open_ports & WINDOWS_PORTS):
        scores["linux_server"] += 20
        signals.append("ssh")
        evidence.append(_evidence_for_port(22))

    if open_ports & {3306, 5432, 6379}:
        scores["linux_server"] += 25
        signals.append("db_ports")
        for port in sorted(open_ports & {3306, 5432, 6379}):
            evidence.append(_evidence_for_port(port))

    if has_smb and open_ports & {5000, 5001}:
        scores["nas"] += 45
        signals.append("nas_ports")
        evidence.append("SMB and NAS web ports open")
    elif open_ports & NAS_PORTS and len(open_ports & NAS_PORTS) >= 3:
        scores["nas"] += 20
        signals.append("nas_ports")

    if 53 in open_ports:
        scores["router"] += 20
        evidence.append(_evidence_for_port(53))

    if open_ports & ROUTER_PORTS and len(open_ports) <= 6:
        scores["router"] += 20
        signals.append("router_ports")

    if vendor:
        vl = vendor.lower()
        if "hikvision" in vl or "dahua" in vl:
            scores["camera"] += 35
            signals.append("vendor_camera")
            evidence.append(f"vendor indicates camera ({vendor})")
        if "apple" in vl:
            if host_label and any(
                token in host_label
                for token in ("iphone", "ipad", "galaxy", "pixel", "phone", "android", "mobile")
            ):
                scores["mobile_phone"] += 45
                signals.append("vendor_apple_mobile")
                evidence.append(f"Apple/mobile device ({vendor})")
            else:
                scores["mac"] += 40
                signals.append("vendor_apple")
                evidence.append(f"Apple device ({vendor})")
        if any(token in vl for token in ("dell", "lenovo", "asus", "acer", "msi", "microsoft")):
            scores["pc"] += 30
            signals.append("vendor_pc")
            evidence.append(f"PC vendor ({vendor})")
        if "cisco" in vl or "mikrotik" in vl or "tp-link" in vl or "ubiquiti" in vl:
            scores["router"] += 20
            scores["access_point"] += 10
            signals.append("vendor_network")
            evidence.append(f"network vendor ({vendor})")
        if "synology" in vl or "qnap" in vl:
            scores["nas"] += 40
            signals.append("vendor_nas")
            evidence.append(f"NAS vendor ({vendor})")
        if "hewlett" in vl or "hp " in vl or "canon" in vl or "epson" in vl:
            scores["printer"] += 30
            signals.append("vendor_printer")
            evidence.append(f"printer vendor ({vendor})")

    if http_title:
        tl = http_title.lower()
        if "router" in tl or "gateway" in tl or "mikrotik" in tl:
            scores["router"] += 25
            signals.append("http_title_router")
            evidence.append(f"HTTP title contains router clue ({http_title[:80]})")
        if "camera" in tl or "ipcam" in tl:
            scores["camera"] += 25
            signals.append("http_title_camera")
            evidence.append(f"HTTP title contains camera clue ({http_title[:80]})")
        if "nvr" in tl:
            scores["nvr"] += 25
            evidence.append(f"HTTP title contains NVR clue ({http_title[:80]})")
        if "dvr" in tl:
            scores["dvr"] += 30
            evidence.append(f"HTTP title contains DVR clue ({http_title[:80]})")
        if "printer" in tl:
            scores["printer"] += 25
            evidence.append(f"HTTP title contains printer clue ({http_title[:80]})")

    if http_server:
        sl = http_server.lower()
        if "nginx" in sl or "apache" in sl:
            scores["website"] += 10
            scores["linux_server"] += 5

    if mdns_name:
        ml = mdns_name.lower()
        if "printer" in ml:
            scores["printer"] += 15
        if "nas" in ml or "synology" in ml:
            scores["nas"] += 20
            evidence.append(f"mDNS name suggests NAS ({mdns_name})")

    if host_label:
        if any(token in host_label for token in ("repeater", "extender", "ap-", "-ap", "wifi")):
            scores["access_point"] += 35
            signals.append("hostname_ap")
            evidence.append(f"hostname suggests access point ({hostname or mdns_name})")
        if any(token in host_label for token in ("vacuum", "robot", "dreame", "roomba")):
            scores["web_interface"] += 15
            signals.append("hostname_iot")
            evidence.append(f"hostname suggests IoT device ({hostname or mdns_name})")
        if any(token in host_label for token in ("galaxy", "iphone", "pixel", "phone", "android", "mobile")):
            scores["mobile_phone"] += 45
            signals.append("hostname_phone")
            evidence.append(f"hostname suggests mobile device ({hostname or mdns_name})")
        if any(token in host_label for token in ("imac", "macbook", "mac-mini", "macbookpro", "macbookair")):
            scores["mac"] += 40
            signals.append("hostname_mac")
            evidence.append(f"hostname suggests Mac ({hostname or mdns_name})")
        if any(token in host_label for token in ("desktop", "pc-", "-pc", "workstation", "optiplex", "thinkcentre")):
            scores["pc"] += 35
            signals.append("hostname_pc")
            evidence.append(f"hostname suggests PC ({hostname or mdns_name})")
        if any(token in host_label for token in ("printer", "canon", "epson", "brother", "hp-")):
            scores["printer"] += 30
            signals.append("hostname_printer")
            evidence.append(f"hostname suggests printer ({hostname or mdns_name})")
        if any(token in host_label for token in ("cam", "ipcam", "nvr", "hikvision", "dahua")):
            scores["camera"] += 30
            signals.append("hostname_camera")
            evidence.append(f"hostname suggests camera ({hostname or mdns_name})")
        if "dvr" in host_label:
            scores["dvr"] += 35
            signals.append("hostname_dvr")
            evidence.append(f"hostname suggests DVR ({hostname or mdns_name})")
        if any(token in host_label for token in ("switch", "sw-", "-sw")):
            scores["switch"] += 35
            signals.append("hostname_switch")
            evidence.append(f"hostname suggests switch ({hostname or mdns_name})")
        if any(token in host_label for token in ("router", "gateway", "cpe", "snr-cpe", "mikrotik")):
            scores["router"] += 30
            signals.append("hostname_router")
            evidence.append(f"hostname suggests router ({hostname or mdns_name})")
        if "docker" in host_label:
            scores["linux_server"] += 20
            signals.append("hostname_docker")
            evidence.append(f"hostname suggests container host ({hostname or mdns_name})")

    if snmp_descr:
        sn = snmp_descr.lower()
        if "switch" in sn:
            scores["switch"] += 35
            evidence.append("SNMP description mentions switch")
        if "router" in sn:
            scores["router"] += 25
            evidence.append("SNMP description mentions router")
        if "camera" in sn or "ipcam" in sn:
            scores["camera"] += 25
            evidence.append("SNMP description mentions camera")

    if 161 in open_ports and len(open_ports) <= 4 and not (open_ports & WEB_PORTS):
        scores["switch"] += 20
        scores["router"] += 10
        evidence.append("SNMP-only or minimal management ports")

    if open_ports & WEB_PORTS and not any(
        scores[t] >= 25 for t in ("camera", "nvr", "router", "nas", "printer", "windows_server", "linux_server")
    ):
        scores["web_interface"] += 25
        evidence.append("HTTP/HTTPS response without stronger device type")

    if open_ports & SERVER_PORTS and scores["linux_server"] < 25 and scores["windows_server"] < 25:
        scores["linux_server"] += 5

    best_type = max(scores, key=lambda k: scores[k])
    best_score = scores[best_type]

    if not open_ports and not vendor and not http_title and not is_gateway and not host_label:
        return ClassificationResult(
            "unknown",
            25,
            signals or ["reachable_only"],
            evidence or ["host responded but no open ports or metadata"],
        )

    if not open_ports and host_label and best_score <= 0:
        best_type = max(scores, key=lambda k: scores[k])
        best_score = scores[best_type]
        if best_score > 0:
            return ClassificationResult(
                best_type,
                min(100, 35 + best_score),
                signals or ["hostname_only"],
                evidence or [f"classified from hostname ({hostname or mdns_name})"],
            )
        return ClassificationResult(
            "unknown",
            40,
            signals or ["hostname_only"],
            evidence or [f"hostname resolved ({hostname or mdns_name})"],
        )

    if best_score <= 0:
        return ClassificationResult(
            "unknown",
            30,
            signals or ["no_signals"],
            evidence or ["reachable with insufficient classification evidence"],
        )

    confidence = min(100, 25 + best_score)

    if best_type == "windows_server" and not (has_rdp or (has_smb and has_rdp)):
        if not (has_rdp or has_smb):
            best_type = "unknown"
            confidence = 30
        elif has_smb and not has_rdp:
            confidence = min(confidence, 55)
            if not vendor and not http_title:
                best_type = "unknown"
                confidence = 40

    if best_type == "linux_server":
        if not has_ssh:
            best_type = "unknown"
            confidence = 30
        elif not (vendor or http_title or mdns_name) and not (open_ports & {3306, 5432, 6379}):
            confidence = min(confidence, 55)

    if best_type in {"camera", "nvr"} and 554 not in open_ports and not onvif_hint:
        confidence = min(confidence, 50)

    if best_type == "web_interface":
        confidence = min(confidence, 60)

    return ClassificationResult(best_type, confidence, signals, evidence)


def classification_from_metadata(metadata: dict[str, Any]) -> ClassificationResult:
    ports = set(metadata.get("open_ports") or [])
    return classify_device(
        open_ports=ports,
        vendor=str(metadata.get("vendor") or ""),
        mac_address=str(metadata.get("mac_address") or ""),
        http_server=str(metadata.get("http_server") or ""),
        http_title=str(metadata.get("http_title") or ""),
        mdns_name=str(metadata.get("mdns_name") or metadata.get("dns_name") or ""),
        hostname=str(
            metadata.get("hostname")
            or metadata.get("dns_name")
            or metadata.get("mdns_name")
            or ""
        ),
        ssdp_type=str(metadata.get("ssdp_type") or ""),
        snmp_descr=str(metadata.get("snmp_descr") or ""),
        onvif_hint=bool(metadata.get("onvif_hint")),
        is_gateway=bool(metadata.get("is_gateway")),
    )
