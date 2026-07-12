"""DNS reverse (PTR) lookups using LAN resolver — Docker's 127.0.0.11 misses .lan names."""

from __future__ import annotations

import asyncio
import random
import socket
import struct
from typing import Iterable


def _encode_name(name: str) -> bytes:
    parts = name.strip(".").split(".")
    out = bytearray()
    for part in parts:
        label = part.encode("ascii", errors="ignore")[:63]
        out.append(len(label))
        out.extend(label)
    out.append(0)
    return bytes(out)


def _decode_name(data: bytes, offset: int) -> tuple[str, int]:
    labels: list[str] = []
    jumped = False
    jump_offset = offset
    while True:
        if offset >= len(data):
            break
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                break
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            if not jumped:
                jump_offset = offset + 2
            offset = pointer
            jumped = True
            continue
        offset += 1
        end = offset + length
        labels.append(data[offset:end].decode("ascii", errors="ignore"))
        offset = end
    return ".".join(labels), (jump_offset if jumped else offset)


def ptr_lookup(ip: str, nameserver: str, *, timeout: float = 2.0) -> str:
    """Query a specific DNS server for PTR record of *ip*."""
    ip = str(ip or "").strip()
    server = str(nameserver or "").strip()
    if not ip or not server:
        return ""

    qname = _encode_name(".".join(reversed(ip.split("."))) + ".in-addr.arpa")
    tid = random.randint(0, 65535)
    header = struct.pack("!HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    question = qname + struct.pack("!HH", 12, 1)
    packet = header + question

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(packet, (server, 53))
        data, _ = sock.recvfrom(4096)
    except OSError:
        return ""
    finally:
        sock.close()

    if len(data) < 12:
        return ""
    resp_id, _, qdcount, ancount, _, _ = struct.unpack("!HHHHHH", data[:12])
    if resp_id != tid or qdcount < 1 or ancount < 1:
        return ""

    offset = 12
    for _ in range(qdcount):
        while offset < len(data) and data[offset] != 0:
            if data[offset] & 0xC0 == 0xC0:
                offset += 2
                break
            offset += data[offset] + 1
        else:
            offset += 1
        offset += 4

    for _ in range(ancount):
        if offset >= len(data):
            break
        name, offset = _decode_name(data, offset)
        if offset + 10 > len(data):
            break
        rtype, _, _, rdlength = struct.unpack("!HHIH", data[offset : offset + 10])
        offset += 10
        rdata = data[offset : offset + rdlength]
        offset += rdlength
        if rtype == 12 and rdata:
            host, _ = _decode_name(data, offset - rdlength)
            clean = str(host or name or "").strip().rstrip(".")
            if clean and clean != ip:
                return clean
    return ""


async def reverse_lookup(ip: str, *, nameservers: Iterable[str], timeout: float = 2.0) -> str:
    """Try system resolver, then explicit LAN DNS servers (router/gateway)."""
    loop = asyncio.get_running_loop()
    try:
        hostname, _, _ = await asyncio.wait_for(
            loop.run_in_executor(None, socket.gethostbyaddr, ip),
            timeout=timeout,
        )
        clean = str(hostname or "").strip().rstrip(".")
        if clean and clean != ip:
            return clean
    except Exception:
        pass

    seen: set[str] = set()
    for server in nameservers:
        server = str(server or "").strip()
        if not server or server in seen:
            continue
        seen.add(server)
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, lambda s=server: ptr_lookup(ip, s, timeout=timeout)),
                timeout=timeout + 0.5,
            )
        except Exception:
            result = ""
        if result:
            return result
    return ""
