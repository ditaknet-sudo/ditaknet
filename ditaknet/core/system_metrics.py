"""Host-level metrics for the DitakNet server process."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ditaknet.config import settings
from ditaknet.core.system_log_service import uptime_seconds

try:
    import psutil
except ImportError:  # pragma: no cover - optional dependency
    psutil = None  # type: ignore[assignment]

_last_net_sample: tuple[float, int, int] | None = None


def _disk_usage_for(path: Path) -> dict[str, Any] | None:
    if psutil is None:
        return None
    try:
        usage = psutil.disk_usage(str(path))
    except Exception:
        return None
    return {
        "path": str(path),
        "total_bytes": int(usage.total),
        "used_bytes": int(usage.used),
        "free_bytes": int(usage.free),
        "percent": round(float(usage.percent), 2),
    }


def _metric_state(value: float | None, *, warn: float, critical: float) -> str:
    if value is None:
        return "unknown"
    if value >= critical:
        return "critical"
    if value >= warn:
        return "warning"
    return "normal"


def collect_system_metrics(*, cpu_interval: float = 0.1) -> dict[str, Any]:
    """Collect CPU, memory, network rates, and mounted directory usage.

    Returns safe fields with null values when a metric cannot be collected.
    """
    global _last_net_sample

    timestamp = datetime.now(timezone.utc).isoformat()
    base: dict[str, Any] = {
        "cpu_percent": None,
        "ram_total": None,
        "ram_used": None,
        "ram_percent": None,
        "disk_total": None,
        "disk_used": None,
        "disk_percent": None,
        "data_dir_disk_percent": None,
        "logs_dir_disk_percent": None,
        "backups_dir_disk_percent": None,
        "network_bytes_sent": None,
        "network_bytes_recv": None,
        "network_upload_rate_bps": None,
        "network_download_rate_bps": None,
        "process_memory_mb": None,
        "process_cpu_percent": None,
        "uptime_seconds": uptime_seconds(),
        "timestamp": timestamp,
        "available": False,
        "reason": None,
        "cpu_state": "unknown",
        "ram_state": "unknown",
        "disk_state": "unknown",
    }

    if psutil is None:
        base["reason"] = "psutil_not_installed"
        return base

    try:
        base["cpu_percent"] = round(float(psutil.cpu_percent(interval=cpu_interval)), 2)
        base["cpu_state"] = _metric_state(base["cpu_percent"], warn=70, critical=90)
    except Exception as exc:
        base["reason"] = f"cpu_unavailable:{type(exc).__name__}"

    try:
        memory = psutil.virtual_memory()
        base["ram_total"] = int(memory.total)
        base["ram_used"] = int(memory.used)
        base["ram_percent"] = round(float(memory.percent), 2)
        base["ram_state"] = _metric_state(base["ram_percent"], warn=75, critical=90)
    except Exception:
        pass

    data_disk = _disk_usage_for(settings.data_dir_path)
    if data_disk:
        base["disk_total"] = data_disk["total_bytes"]
        base["disk_used"] = data_disk["used_bytes"]
        base["disk_percent"] = data_disk["percent"]
        base["data_dir_disk_percent"] = data_disk["percent"]
        base["disk_state"] = _metric_state(base["disk_percent"], warn=80, critical=90)

    logs_disk = _disk_usage_for(settings.log_dir_path)
    if logs_disk:
        base["logs_dir_disk_percent"] = logs_disk["percent"]

    backup_disk = _disk_usage_for(settings.backup_dir_path)
    if backup_disk:
        base["backups_dir_disk_percent"] = backup_disk["percent"]

    try:
        counters = psutil.net_io_counters()
        now = time.monotonic()
        base["network_bytes_sent"] = int(counters.bytes_sent)
        base["network_bytes_recv"] = int(counters.bytes_recv)
        if _last_net_sample is not None:
            elapsed = now - _last_net_sample[0]
            if elapsed > 0:
                base["network_upload_rate_bps"] = round(
                    (counters.bytes_sent - _last_net_sample[1]) / elapsed,
                    2,
                )
                base["network_download_rate_bps"] = round(
                    (counters.bytes_recv - _last_net_sample[2]) / elapsed,
                    2,
                )
        _last_net_sample = (now, int(counters.bytes_sent), int(counters.bytes_recv))
    except Exception:
        pass

    try:
        proc = psutil.Process()
        base["process_memory_mb"] = round(proc.memory_info().rss / (1024 * 1024), 2)
        base["process_cpu_percent"] = round(float(proc.cpu_percent(interval=0)), 2)
    except Exception:
        pass

    base["available"] = any(
        value is not None
        for value in (
            base["cpu_percent"],
            base["ram_percent"],
            base["disk_percent"],
            base["network_bytes_sent"],
        )
    )
    if base["available"]:
        base["reason"] = None
    elif base["reason"] is None:
        base["reason"] = "metrics_partially_unavailable"

    return base
