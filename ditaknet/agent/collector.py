"""
psutil metrics collector for remote agents.

Runs on monitored hosts (not necessarily on the server). Disk path defaults differ
on Windows vs Linux so TrueNAS/Linux agents and Windows dev machines both work.
Server-side API receives percentages only — no raw process lists yet.
"""

from __future__ import annotations

import platform
from typing import Optional

try:
    import psutil
except ImportError:  # pragma: no cover - optional on server, required on agent host
    psutil = None  # type: ignore[assignment]


def default_disk_path(custom_path: Optional[str] = None) -> str:
    """Pick a sensible root mount/path for disk usage."""
    if custom_path:
        return custom_path
    if platform.system().lower() == "windows":
        return "C:\\"
    return "/"


def collect_system_metrics(
    *,
    disk_path: Optional[str] = None,
    cpu_interval: float = 0.1,
) -> dict[str, float]:
    """Collect CPU, memory, and disk utilization percentages."""
    if psutil is None:
        raise RuntimeError("psutil is required to collect system metrics")

    cpu_percent = float(psutil.cpu_percent(interval=cpu_interval))
    memory_percent = float(psutil.virtual_memory().percent)
    disk = psutil.disk_usage(default_disk_path(disk_path))
    disk_percent = float(disk.percent)

    return {
        "cpu_percent": round(cpu_percent, 2),
        "memory_percent": round(memory_percent, 2),
        "disk_percent": round(disk_percent, 2),
    }
