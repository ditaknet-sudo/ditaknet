"""Safe network scanner — ping, TCP connect, basic HTTP metadata."""

from __future__ import annotations

import asyncio
import platform
import re
import socket
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

import httpx

from ditaknet.config import settings
from ditaknet.discovery.arp_table import read_arp_table
from ditaknet.discovery.classifier import classify_device
from ditaknet.discovery.diagnostics import gateway_for_subnet, running_in_container
from ditaknet.discovery.dns_lookup import reverse_lookup as dns_reverse_lookup
from ditaknet.discovery.oui import lookup_vendor
from ditaknet.discovery.ports import ScanProfile, ports_for_profile
from ditaknet.discovery.subnet import iter_hosts

_TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.I)


@dataclass
class ScanProgress:
    total: int = 0
    scanned: int = 0
    found: int = 0
    failed_probes: int = 0
    cancelled: bool = False
    current_ip: str = ""
    current_subnet: str = ""
    stage: str = "preparing"
    stage_message: str = "Preparing discovery scan"
    probe_methods: list[str] = field(default_factory=list)
    permission_errors: list[str] = field(default_factory=list)
    diagnostics: list[dict[str, str]] = field(default_factory=list)
    gateway_ip: str = ""
    gateway_checked: bool = False
    gateway_reachable: bool | None = None
    container_limited: bool = field(default_factory=running_in_container)
    started_monotonic: float = field(default_factory=time.monotonic)

    @property
    def percent(self) -> int:
        if self.total <= 0:
            return 0
        return min(100, int((self.scanned / self.total) * 100))

    @property
    def elapsed_seconds(self) -> int:
        return max(0, int(time.monotonic() - self.started_monotonic))

    def add_permission_error(self, message: str) -> None:
        clean = message.strip()
        if clean and clean not in self.permission_errors:
            self.permission_errors.append(clean[:240])


@dataclass
class DiscoveredHost:
    ip_address: str
    mac_address: str = ""
    hostname: str = ""
    vendor: str = ""
    open_ports: list[int] = field(default_factory=list)
    detected_services: list[str] = field(default_factory=list)
    detected_type: str = "unknown"
    confidence: int = 0
    discovery_source: str = "tcp_ping"
    raw_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PingResult:
    reachable: bool
    error: str = ""
    permission_error: bool = False


async def _ping_host(ip: str, timeout: float) -> PingResult:
    """ICMP ping via system ping (same approach as PingCheck)."""
    system = platform.system().lower()
    if system == "windows":
        cmd = ["ping", "-n", "1", "-w", str(int(timeout * 1000)), ip]
    else:
        cmd = ["ping", "-c", "1", "-W", str(max(1, int(timeout))), ip]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout + 1)
        if proc.returncode == 0:
            return PingResult(True)
        text = (stderr or stdout or b"").decode(errors="ignore").strip()
        lowered = text.lower()
        permission_error = any(
            marker in lowered
            for marker in ("operation not permitted", "permission denied", "raw socket", "not allowed")
        )
        return PingResult(False, text[:240], permission_error)
    except asyncio.TimeoutError:
        return PingResult(False, "ICMP ping timed out")
    except FileNotFoundError:
        return PingResult(False, "System ping command not found")
    except PermissionError as exc:
        return PingResult(False, str(exc), True)
    except OSError as exc:
        text = str(exc)
        lowered = text.lower()
        return PingResult(
            False,
            text,
            any(marker in lowered for marker in ("operation not permitted", "permission denied")),
        )


async def _tcp_open(ip: str, port: int, timeout: float) -> bool:
    try:
        conn = asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
        reader, writer = await conn
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def _http_metadata(ip: str, port: int, timeout: float, use_https: bool) -> dict[str, str]:
    scheme = "https" if use_https else "http"
    url = f"{scheme}://{ip}:{port}/"
    result: dict[str, str] = {}
    try:
        async with httpx.AsyncClient(
            verify=False,
            follow_redirects=True,
            timeout=timeout,
        ) as client:
            resp = await client.get(url)
            result["http_status"] = str(resp.status_code)
            result["http_server"] = resp.headers.get("Server", "")
            match = _TITLE_RE.search(resp.text[:8192])
            if match:
                result["http_title"] = match.group(1).strip()[:200]
    except Exception:
        pass
    return result


def _discovery_nameservers(gateway_ip: str) -> list[str]:
    servers: list[str] = []
    if gateway_ip:
        servers.append(gateway_ip)
    extra = str(settings.discovery_dns_servers or "").strip()
    if extra:
        servers.extend(item.strip() for item in extra.split(",") if item.strip())
    return servers


def _query_netbios(ip: str, timeout: float) -> tuple[str, str]:
    """Synchronous NetBIOS query designed to run in executor."""
    payload = b'\xa1\xb2\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x20CKCACACACACACACACACACACACACACACA\x00\x00\x21\x00\x01'
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(payload, (ip, 137))
        data, addr = sock.recvfrom(1024)
        if len(data) < 56:
            return "", ""
        if data[0:2] != b'\xa1\xb2':
            return "", ""

        offset = 12
        if data[offset] == 0x20:
            offset += 34
        else:
            return "", ""
        offset += 4

        if offset < len(data) and (data[offset] & 0xC0) == 0xC0:
            offset += 2
        else:
            while offset < len(data) and data[offset] != 0:
                offset += data[offset] + 1
            offset += 1

        if offset + 10 > len(data):
            return "", ""

        rr_type = int.from_bytes(data[offset : offset + 2], "big")
        offset += 8
        rdata_len = int.from_bytes(data[offset : offset + 2], "big")
        offset += 2

        if rr_type != 0x21 or offset + rdata_len > len(data):
            return "", ""

        num_names = data[offset]
        offset += 1

        hostname = ""
        for _ in range(num_names):
            if offset + 18 > len(data):
                break
            name_bytes = data[offset : offset + 15]
            try:
                name_str = name_bytes.decode("ascii", errors="ignore").strip()
            except Exception:
                name_str = ""
            if name_str and not hostname:
                if not name_str.startswith("\x01") and not name_str.startswith("\x02"):
                    hostname = name_str
            offset += 18

        mac = ""
        if offset + 6 <= len(data):
            mac_bytes = data[offset : offset + 6]
            mac = ":".join(f"{b:02X}" for b in mac_bytes)
            if mac == "00:00:00:00:00:00":
                mac = ""

        return hostname, mac
    except Exception:
        pass
    finally:
        sock.close()
    return "", ""


async def _netbios_probe(ip: str, timeout: float) -> tuple[str, str]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _query_netbios, ip, timeout)


async def _mdns_probe(ip: str, timeout: float) -> str:
    """Reverse lookup via getnameinfo (best-effort; often empty on Windows)."""
    try:
        loop = asyncio.get_running_loop()
        name, _, _ = await asyncio.wait_for(loop.getnameinfo((ip, 0), 0), timeout=timeout)
        return name or ""
    except Exception:
        return ""


async def _dns_reverse_lookup(ip: str, timeout: float, *, nameservers: list[str]) -> str:
    """DNS PTR via router/LAN resolver (Docker embedded DNS misses .lan names)."""
    return await dns_reverse_lookup(ip, nameservers=nameservers, timeout=timeout)


def _resolve_mac(ip: str, arp: dict[str, str], netbios_mac: str = "") -> str:
    """Prefer cached ARP, refresh table after ping, then NetBIOS MAC."""
    mac = arp.get(ip, "") or netbios_mac
    if mac:
        return mac
    fresh = read_arp_table()
    arp.update(fresh)
    return arp.get(ip, "") or netbios_mac


async def _ssdp_placeholder(ip: str) -> str:
    """SSDP detection placeholder — returns empty until full safe implementation."""
    return ""


async def _snmp_placeholder(ip: str) -> str:
    """SNMP sysDescr placeholder — no community guessing."""
    return ""


async def _onvif_hint(ip: str, open_ports: set[int]) -> bool:
    """Heuristic ONVIF hint from open ports only (no auth probes)."""
    return bool(open_ports & {80, 443, 8000, 8080} and 554 in open_ports)


class NetworkScanner:
    """Rate-limited scanner for authorized private subnets."""

    def __init__(
        self,
        profile: ScanProfile = "normal",
        *,
        max_concurrent: int | None = None,
        timeout_seconds: float | None = None,
        batch_pause_ms: int | None = None,
    ):
        self.profile = profile
        self.max_concurrent = max_concurrent or settings.discovery_max_concurrent
        self.timeout_seconds = timeout_seconds or settings.discovery_timeout_seconds
        self.batch_pause_ms = batch_pause_ms or settings.discovery_batch_pause_ms
        self.ports = ports_for_profile(profile)
        self._sem = asyncio.Semaphore(self.max_concurrent)

    async def scan_subnets(
        self,
        subnets: list[str],
        progress: ScanProgress,
        on_host: Callable[[DiscoveredHost], Awaitable[None]] | None = None,
        on_progress: Callable[[ScanProgress], Awaitable[None]] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> list[DiscoveredHost]:
        hosts: list[tuple[str, str]] = []
        for cidr in subnets:
            for ip in iter_hosts(cidr):
                hosts.append((ip, cidr))
        progress.total = len(hosts)
        progress.probe_methods = ["icmp_ping", "tcp_connect"]
        if subnets:
            progress.gateway_ip = gateway_for_subnet(subnets[0])
        self._nameservers = _discovery_nameservers(progress.gateway_ip)
        arp = read_arp_table()
        results: list[DiscoveredHost] = []
        batch_size = max(self.max_concurrent * 2, 4)

        for batch_start in range(0, len(hosts), batch_size):
            if should_cancel and should_cancel():
                progress.cancelled = True
                break
            batch = hosts[batch_start : batch_start + batch_size]
            if batch:
                progress.current_subnet = batch[0][1]
            progress.stage = "scanning_hosts"
            progress.stage_message = f"Scanning {progress.current_subnet or 'selected subnet'}"
            tasks = [
                self._scan_one(ip, cidr, arp, progress, should_cancel, on_progress)
                for ip, cidr in batch
            ]
            batch_results = await asyncio.gather(*tasks)
            for item in batch_results:
                if item:
                    results.append(item)
                    progress.found += 1
                    if on_host:
                        await on_host(item)
            if self.batch_pause_ms > 0:
                await asyncio.sleep(self.batch_pause_ms / 1000.0)

        return results

    async def _scan_one(
        self,
        ip: str,
        cidr: str,
        arp: dict[str, str],
        progress: ScanProgress,
        should_cancel: Callable[[], bool] | None,
        on_progress: Callable[[ScanProgress], Awaitable[None]] | None,
    ) -> DiscoveredHost | None:
        if should_cancel and should_cancel():
            return None
        async with self._sem:
            progress.current_ip = ip
            progress.current_subnet = cidr
            try:
                ping = await _ping_host(ip, self.timeout_seconds)
                reachable = ping.reachable
                if not ping.reachable:
                    progress.failed_probes += 1
                if ping.permission_error:
                    progress.add_permission_error(ping.error or "ICMP permission denied")
                port_tasks = [
                    _tcp_open(ip, port, self.timeout_seconds) for port in self.ports
                ]
                port_results = await asyncio.gather(*port_tasks)
                open_ports = {
                    port for port, ok in zip(self.ports, port_results) if ok
                }
                progress.failed_probes += len(self.ports) - len(open_ports)
                if ip == progress.gateway_ip:
                    progress.gateway_checked = True
                    progress.gateway_reachable = bool(reachable or open_ports)
                if not reachable and not open_ports:
                    return None

                netbios_name = ""
                netbios_mac = ""
                try:
                    netbios_name, netbios_mac = await _netbios_probe(ip, min(1.0, self.timeout_seconds))
                except Exception:
                    pass

                mac = _resolve_mac(ip, arp, netbios_mac)

                dns_name = await _dns_reverse_lookup(
                    ip,
                    min(2.0, self.timeout_seconds),
                    nameservers=self._nameservers,
                )

                vendor = lookup_vendor(mac)
                metadata: dict[str, Any] = {
                    "open_ports": sorted(open_ports),
                    "netbios_name": netbios_name,
                    "dns_name": dns_name,
                }

                if self.profile in ("normal", "deep") and open_ports & {80, 443, 8080, 8443}:
                    progress.stage = "checking_ports"
                    http_port = 443 if 443 in open_ports else 80 if 80 in open_ports else 8080
                    meta = await _http_metadata(
                        ip,
                        http_port,
                        self.timeout_seconds,
                        use_https=http_port in (443, 8443),
                    )
                    metadata.update(meta)

                mdns_name = ""
                ssdp_type = ""
                snmp_descr = ""
                if self.profile == "deep":
                    progress.stage = "classifying_devices"
                    mdns_name = await _mdns_probe(ip, self.timeout_seconds)
                    ssdp_type = await _ssdp_placeholder(ip)
                    snmp_descr = await _snmp_placeholder(ip)
                    metadata["mdns_name"] = mdns_name
                    metadata["ssdp_type"] = ssdp_type
                    metadata["snmp_descr"] = snmp_descr

                onvif = await _onvif_hint(ip, open_ports)
                metadata["onvif_hint"] = onvif
                metadata["mac_address"] = mac
                metadata["vendor"] = vendor

                resolved_hostname = netbios_name or dns_name or mdns_name
                classification = classify_device(
                    open_ports=open_ports,
                    vendor=vendor,
                    mac_address=mac,
                    http_server=str(metadata.get("http_server") or ""),
                    http_title=str(metadata.get("http_title") or ""),
                    hostname=resolved_hostname,
                    mdns_name=resolved_hostname,
                    ssdp_type=ssdp_type,
                    snmp_descr=snmp_descr,
                    onvif_hint=onvif,
                    is_gateway=ip == progress.gateway_ip,
                )
                metadata["evidence"] = classification.evidence
                metadata["signals"] = classification.signals

                services = []
                if 22 in open_ports:
                    services.append("ssh")
                if 80 in open_ports or 443 in open_ports:
                    services.append("http")
                if 554 in open_ports:
                    services.append("rtsp")

                host = DiscoveredHost(
                    ip_address=ip,
                    mac_address=mac,
                    hostname=resolved_hostname,
                    vendor=vendor,
                    open_ports=sorted(open_ports),
                    detected_services=services,
                    detected_type=classification.detected_type,
                    confidence=classification.confidence,
                    discovery_source="ping_tcp" if reachable else "tcp",
                    raw_metadata=metadata,
                )
                return host
            finally:
                progress.scanned += 1
                if on_progress:
                    await on_progress(progress)
