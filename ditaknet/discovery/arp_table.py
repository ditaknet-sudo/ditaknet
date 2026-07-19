"""Merge ARP/MAC sources: kernel table, arp command, and optional host sync file."""

from __future__ import annotations

import json
import platform
import re
import subprocess
from pathlib import Path
from typing import Any

from loguru import logger

from ditaknet.config import settings


def _parse_arp_lines(text: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in text.splitlines():
        ip_m = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", line)
        mac_m = re.search(
            r"\b([0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2})\b",
            line,
        )
        if not ip_m or not mac_m:
            continue
        ip = ip_m.group(1)
        mac = mac_m.group(1).upper().replace("-", ":")
        if mac in ("00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF"):
            continue
        mapping[ip] = mac
    return mapping


def _read_proc_arp() -> dict[str, str]:
    if platform.system().lower() != "linux":
        return {}
    arp_path = Path("/proc/net/arp")
    if not arp_path.exists():
        return {}
    mapping: dict[str, str] = {}
    for line in arp_path.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4 and parts[3] != "00:00:00:00:00:00":
            mapping[parts[0]] = parts[3].upper().replace("-", ":")
    return mapping


def _read_arp_command() -> dict[str, str]:
    try:
        output = subprocess.check_output(["arp", "-a"], text=True, timeout=3, errors="ignore")
    except Exception:
        return {}
    return _parse_arp_lines(output)


def _read_host_sync_file() -> dict[str, str]:
    """Optional JSON map written by scripts/sync-host-arp.ps1 on the Windows host."""
    candidates = [
        settings.data_dir_path / "host_arp.json",
        Path(settings.data_dir) / "host_arp.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Invalid host ARP sync file {}: {}", path, exc)
            continue
        if isinstance(payload, dict):
            return {
                str(ip).strip(): str(mac).upper().replace("-", ":")
                for ip, mac in payload.items()
                if ip and mac
            }
    return {}


def read_arp_table() -> dict[str, str]:
    """Best-effort merged ARP table for the monitored LAN."""
    merged: dict[str, str] = {}
    for source in (_read_proc_arp(), _read_arp_command(), _read_host_sync_file()):
        merged.update(source)
    return merged
