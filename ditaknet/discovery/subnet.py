"""Private subnet detection and validation."""

from __future__ import annotations

import ipaddress
import socket
from typing import Iterable

import psutil

# RFC1918 + link-local; public ranges are rejected for scanning.
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
)
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")

_DEFAULT_SUBNET_BY_TYPE = {
    "192.168": "192.168.1.0/24",
    "10": "10.0.0.0/24",
    "172": "172.16.0.0/24",
}


def is_private_ip(ip: str) -> bool:
    """Return True if *ip* is a private/link-local address."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in _PRIVATE_NETWORKS)


def private_ip_address(value: str) -> ipaddress.IPv4Address | None:
    """Return a private IPv4 address from *value*, or None for hostnames/public IPs."""
    try:
        addr = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if addr.version != 4 or not is_private_ip(str(addr)):
        return None
    return addr


def private_ip_network(value: str, prefix: int = 24) -> str | None:
    """Infer a private IPv4 network for a device address."""
    addr = private_ip_address(value)
    if not addr:
        return None
    return str(ipaddress.ip_network(f"{addr}/{prefix}", strict=False))


def address_in_subnet(address: str, cidr: str) -> bool:
    """Return True when a private IPv4 address belongs to *cidr*."""
    addr = private_ip_address(address)
    if not addr:
        return False
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False
    return addr in network


def subnet_within(candidate: str, allowed: str) -> bool:
    """Return True when candidate is inside or equal to allowed."""
    try:
        candidate_net = ipaddress.ip_network(candidate, strict=False)
        allowed_net = ipaddress.ip_network(allowed, strict=False)
    except ValueError:
        return False
    return candidate_net.subnet_of(allowed_net)


def is_private_subnet(cidr: str) -> bool:
    """Return True when *cidr* is a private network (RFC1918 / link-local).

    Any private prefix length is accepted (/8 … /32), including single-host /32.
    """
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False
    return bool(network.is_private)


def is_cgnat_subnet(cidr: str) -> bool:
    """Return True when *cidr* is inside the CGNAT carrier-grade range."""
    try:
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False
    return network.subnet_of(_CGNAT_NETWORK)


def suggest_subnet_for_type(network_type: str, local_subnets: list[str]) -> str:
    """Suggest a default private subnet for setup based on network type."""
    prefix = {"192.168": "192.168.", "10": "10.", "172": "172."}.get(network_type, "")
    if prefix and local_subnets:
        matching = [s for s in local_subnets if s.startswith(prefix)]
        if matching:
            return pick_primary_subnet(matching)
    if local_subnets:
        return pick_primary_subnet(local_subnets)
    return _DEFAULT_SUBNET_BY_TYPE.get(network_type, "192.168.1.0/24")


def subnet_host_count(cidr: str) -> int:
    """Count usable host addresses in a CIDR (for license limits)."""
    network = ipaddress.ip_network(cidr, strict=False)
    if network.version == 4 and network.prefixlen >= 31:
        return network.num_addresses
    if network.version == 4:
        return max(network.num_addresses - 2, 0)
    return network.num_addresses


def prefix_length(cidr: str) -> int:
    return ipaddress.ip_network(cidr, strict=False).prefixlen


def detect_local_subnets() -> list[str]:
    """Infer private /24 (or shorter prefix) subnets from local interfaces."""
    found: set[str] = set()
    for _iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family != socket.AF_INET or not addr.address:
                continue
            if not is_private_ip(addr.address):
                continue
            if addr.netmask:
                try:
                    iface = ipaddress.ip_interface(f"{addr.address}/{addr.netmask}")
                    found.add(str(iface.network))
                    continue
                except ValueError:
                    pass
            # Fallback: assume /24 for typical homelab LANs.
            parts = addr.address.split(".")
            if len(parts) == 4:
                found.add(f"{parts[0]}.{parts[1]}.{parts[2]}.0/24")
    return sorted(found)


def iter_hosts(cidr: str) -> Iterable[str]:
    """Yield host IPs in *cidr* (skips network/broadcast on typical IPv4 LANs).

    /31 and /32 include every address in the network so single-host CIDRs scan.
    """
    network = ipaddress.ip_network(cidr, strict=False)
    if network.version == 4 and network.prefixlen >= 31:
        for addr in network:
            yield str(addr)
        return
    for host in network.hosts():
        yield str(host)


def normalize_subnets(subnets: list[str]) -> list[str]:
    """Validate and deduplicate subnet list; raises ValueError if invalid."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in subnets:
        cidr = raw.strip()
        if not cidr:
            continue
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError as exc:
            raise ValueError(f"Invalid subnet: {cidr}") from exc
        if not is_private_subnet(str(net)):
            raise ValueError(f"Only private subnets may be scanned: {cidr}")
        key = str(net)
        if key not in seen:
            seen.add(key)
            normalized.append(key)
    return normalized


def _subnet_preference_rank(cidr: str) -> tuple[int, int]:
    """Lower rank = better default when license allows only one subnet."""
    net = ipaddress.ip_network(cidr, strict=False)
    text = str(net)
    if text.startswith("169.254."):
        return (90, net.prefixlen)
    if text.startswith("172.17.") or text.startswith("172.18."):
        return (70, net.prefixlen)
    if net.prefixlen < 20:
        return (60, net.prefixlen)
    if text.startswith("192.168."):
        return (0, abs(net.prefixlen - 24))
    if text.startswith("10."):
        return (10, abs(net.prefixlen - 24))
    return (20, abs(net.prefixlen - 24))


def pick_primary_subnet(subnets: list[str]) -> str:
    """Pick the most likely home/office LAN from a list of private subnets."""
    normalized = normalize_subnets(subnets)
    if not normalized:
        raise ValueError("Enter a private subnet such as 192.168.1.0/24")
    return min(normalized, key=_subnet_preference_rank)


def limit_subnets_for_scan(subnets: list[str], max_count: int | None) -> list[str]:
    """Normalize subnets and trim to the license limit, preferring typical LAN /24."""
    normalized = normalize_subnets(subnets)
    if not max_count or len(normalized) <= max_count:
        return normalized
    if max_count == 1:
        return [pick_primary_subnet(normalized)]
    ranked = sorted(normalized, key=_subnet_preference_rank)
    return ranked[:max_count]
