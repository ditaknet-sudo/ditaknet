"""Device monitoring overview, uptime, and heartbeat calculations from real check history."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ditaknet import database as db

CHECK_PRIORITY = ("ping", "tcp", "http", "https", "dns", "rtsp")
STATUS_PRIORITY = {"critical": 4, "down": 4, "error": 4, "warning": 3, "unknown": 2, "ok": 1}
DISPLAY_STATUS = {
    "ok": "UP",
    "warning": "WARNING",
    "critical": "DOWN",
    "unknown": "UNKNOWN",
    "disabled": "DISABLED",
}


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _now() -> datetime:
    return datetime.now(UTC)


def _status_bucket(status: str | None) -> str:
    norm = str(status or "unknown").lower()
    if norm == "ok":
        return "ok"
    if norm == "warning":
        return "warning"
    if norm in {"critical", "down", "error"}:
        return "critical"
    return "unknown"


def pick_primary_service(services: list[dict[str, Any]]) -> dict[str, Any] | None:
    enabled = [s for s in services if s.get("enabled")]
    if not enabled:
        return services[0] if services else None
    for check_type in CHECK_PRIORITY:
        for svc in enabled:
            if str(svc.get("check_type") or "").lower() == check_type:
                return svc
    return enabled[0]


def compute_device_status(host: dict[str, Any], services: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive device-level status from enabled services and host state."""
    if not host.get("enabled"):
        return {"state": "disabled", "display": "DISABLED", "reason": "monitoring_disabled"}

    enabled = [s for s in services if s.get("enabled")]
    if not enabled:
        return {"state": "unknown", "display": "UNKNOWN", "reason": "no_enabled_checks"}

    states = [str(s.get("current_state") or "unknown").lower() for s in enabled]
    if any(s in {"critical", "down", "error"} for s in states):
        return {"state": "critical", "display": "DOWN", "reason": "critical_check_failed"}
    if any(s == "warning" for s in states):
        return {"state": "warning", "display": "WARNING", "reason": "partial_failure"}
    if all(s == "ok" for s in states):
        return {"state": "ok", "display": "UP", "reason": "all_checks_ok"}
    return {"state": "unknown", "display": "UNKNOWN", "reason": "no_check_history"}


def uptime_percent(rows: list[dict[str, Any]]) -> float | None:
    if not rows:
        return None
    ok_count = sum(1 for row in rows if _status_bucket(row.get("status")) == "ok")
    return round(100.0 * ok_count / len(rows), 2)


def _safe_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def average_response_ms(rows: list[dict[str, Any]]) -> float | None:
    values = [
        ms
        for ms in (_safe_float(r.get("response_time_ms")) for r in rows)
        if ms is not None
    ]
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def build_heartbeat_bars(
    rows: list[dict[str, Any]],
    *,
    hours: int,
    buckets: int = 48,
) -> list[dict[str, Any]]:
    """Build Uptime-Kuma-style heartbeat buckets from real check rows."""
    end = _now()
    start = end - timedelta(hours=hours)
    bucket_seconds = max(int(hours * 3600 / buckets), 60)
    slots: list[dict[str, Any]] = []
    for index in range(buckets):
        slot_start = start + timedelta(seconds=index * bucket_seconds)
        slot_end = slot_start + timedelta(seconds=bucket_seconds)
        slots.append(
            {
                "start": slot_start.isoformat(),
                "end": slot_end.isoformat(),
                "status": "unknown",
                "checks": 0,
                "ok": 0,
                "warning": 0,
                "critical": 0,
                "avg_response_ms": None,
                "sample_at": None,
                "sample_status": None,
            }
        )

    for row in rows:
        checked = _parse_ts(row.get("checked_at"))
        if not checked or checked < start or checked >= end:
            continue
        offset = int((checked - start).total_seconds() // bucket_seconds)
        if offset < 0 or offset >= buckets:
            continue
        slot = slots[offset]
        slot["checks"] += 1
        bucket = _status_bucket(row.get("status"))
        slot[bucket if bucket in {"ok", "warning", "critical"} else "critical"] += 1
        if slot["sample_at"] is None or row.get("checked_at") > slot["sample_at"]:
            slot["sample_at"] = row.get("checked_at")
            slot["sample_status"] = row.get("status")
            if row.get("response_time_ms") is not None:
                slot["avg_response_ms"] = float(row["response_time_ms"])

    for slot in slots:
        if slot["checks"] == 0:
            slot["status"] = "unknown"
            continue
        if slot["critical"] > 0:
            slot["status"] = "critical"
        elif slot["warning"] > 0:
            slot["status"] = "warning"
        elif slot["ok"] > 0:
            slot["status"] = "ok"
        else:
            slot["status"] = "unknown"
    return slots


def format_downtime_seconds(seconds: int | float | None) -> str | None:
    if seconds is None:
        return None
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def _incident_stats(rows: list[dict[str, Any]], state_changes: list[dict[str, Any]]) -> dict[str, Any]:
    incidents = 0
    last_down_at = None
    last_recovery_at = None
    total_downtime_seconds = 0

    for change in state_changes:
        new_state = str(change.get("new_state") or "").lower()
        old_state = str(change.get("old_state") or "").lower()
        changed_at = change.get("changed_at")
        if new_state in {"critical", "down", "warning"} and old_state == "ok":
            incidents += 1
            if last_down_at is None or (changed_at and changed_at > last_down_at):
                last_down_at = changed_at
        if new_state == "ok" and old_state in {"critical", "down", "warning"}:
            last_recovery_at = changed_at

    failed_rows = [r for r in rows if _status_bucket(r.get("status")) != "ok"]
    if failed_rows and not last_down_at:
        last_down_at = failed_rows[0].get("checked_at")

    # Approximate downtime as failed check intervals (conservative, real data only).
    for row in failed_rows:
        if row.get("response_time_ms") is None and _status_bucket(row.get("status")) == "critical":
            total_downtime_seconds += 60

    return {
        "incident_count_24h": incidents,
        "last_down_at": last_down_at,
        "last_recovery_at": last_recovery_at,
        "total_downtime_seconds": total_downtime_seconds,
        "total_downtime_display": format_downtime_seconds(total_downtime_seconds),
    }


async def _host_history(host_id: int, hours: int, *, limit: int = 20000) -> list[dict[str, Any]]:
    since = (_now() - timedelta(hours=hours)).isoformat()
    return await db.list_host_check_history_since(host_id, since=since, limit=limit)


async def build_service_checks(host_id: int) -> list[dict[str, Any]]:
    services = await db.list_services(host_id)
    output: list[dict[str, Any]] = []
    since_24h = (_now() - timedelta(hours=24)).isoformat()
    for svc in services:
        latest = await db.get_latest_check(svc["id"])
        rows = await db.list_check_results(service_id=svc["id"], limit=500)
        rows_24h = [r for r in rows if r.get("checked_at") and r["checked_at"] >= since_24h]
        output.append(
            {
                "id": svc["id"],
                "name": svc["name"],
                "check_type": svc["check_type"],
                "target": svc["target"],
                "port": svc.get("port"),
                "enabled": bool(svc.get("enabled")),
                "interval_seconds": svc.get("interval_seconds"),
                "current_state": svc.get("current_state"),
                "latest_check": latest,
                "uptime_24h": uptime_percent(rows_24h),
                "avg_response_24h": average_response_ms(rows_24h),
            }
        )
    return output


async def build_recent_events(host_id: int, *, limit: int = 30) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    services = {s["id"]: s for s in await db.list_services(host_id)}

    for alert in await db.list_alerts_for_host(host_id, limit=limit):
        svc = services.get(alert.get("service_id"), {})
        event_type = "device_recovered" if alert.get("resolved_at") else "device_down"
        if str(alert.get("severity") or "").lower() == "warning":
            event_type = "warning_detected"
        events.append(
            {
                "at": alert.get("resolved_at") or alert.get("created_at"),
                "type": event_type,
                "message": alert.get("message") or "",
                "service_name": svc.get("name") or "",
                "severity": alert.get("severity"),
            }
        )

    for change in await db.list_state_changes_for_host(host_id, limit=limit):
        svc = services.get(change.get("service_id"), {})
        old_state = str(change.get("old_state") or "").lower()
        new_state = str(change.get("new_state") or "").lower()
        if new_state in {"critical", "down"} and old_state == "ok":
            event_type = "service_failed"
        elif new_state == "ok" and old_state in {"critical", "down", "warning"}:
            event_type = "service_recovered"
        elif new_state == "warning":
            event_type = "warning_detected"
        else:
            event_type = "state_change"
        events.append(
            {
                "at": change.get("changed_at"),
                "type": event_type,
                "message": change.get("reason") or f"{old_state} → {new_state}",
                "service_name": svc.get("name") or "",
                "severity": new_state,
            }
        )

    events.sort(key=lambda item: str(item.get("at") or ""), reverse=True)
    return events[:limit]


async def build_device_overview(host_id: int) -> dict[str, Any]:
    host = await db.get_host(host_id)
    if not host:
        raise ValueError("Device not found")
    services = await db.list_services(host_id)
    status = compute_device_status(host, services)
    primary = pick_primary_service(services)
    primary_latest = await db.get_latest_check(primary["id"]) if primary else None

    rows_24h = await _host_history(host_id, 24)
    rows_7d = await _host_history(host_id, 24 * 7, limit=50000)
    rows_30d = await _host_history(host_id, 24 * 30, limit=80000)
    state_changes_24h = await db.list_state_changes_for_host(host_id, since_hours=24, limit=500)
    incidents = _incident_stats(rows_24h, state_changes_24h)

    last_seen = host.get("updated_at") or (primary_latest or {}).get("checked_at")
    return {
        "device_id": f"host-{host_id}",
        "source": "host",
        "id": host_id,
        "name": host.get("name"),
        "address": host.get("address"),
        "device_type": host.get("host_type") or "server",
        "location": host.get("location") or "",
        "tags": host.get("tags") or "",
        "enabled": bool(host.get("enabled")),
        "status": status,
        "primary_service": {
            "id": primary.get("id") if primary else None,
            "name": primary.get("name") if primary else None,
            "check_type": primary.get("check_type") if primary else None,
            "interval_seconds": primary.get("interval_seconds") if primary else None,
        },
        "last_seen": last_seen,
        "last_check_at": (primary_latest or {}).get("checked_at"),
        "response_time_ms": (primary_latest or {}).get("response_time_ms"),
        "message": (primary_latest or {}).get("message") or "",
        "has_history": bool(rows_24h or rows_7d),
        "stats": {
            "current_response_ms": (primary_latest or {}).get("response_time_ms"),
            "avg_response_24h": average_response_ms(rows_24h),
            "uptime_24h": uptime_percent(rows_24h),
            "uptime_7d": uptime_percent(rows_7d),
            "uptime_30d": uptime_percent(rows_30d),
            **incidents,
        },
        "services_total": len(services),
        "services_enabled": sum(1 for s in services if s.get("enabled")),
    }


async def build_checks_history(host_id: int, *, limit: int = 200) -> dict[str, Any]:
    rows = await db.list_host_check_history_since(
        host_id,
        since=(_now() - timedelta(days=30)).isoformat(),
        limit=limit,
    )
    return {"device_id": f"host-{host_id}", "items": rows, "total": len(rows)}


async def build_uptime_payload(host_id: int) -> dict[str, Any]:
    return {
        "device_id": f"host-{host_id}",
        "bars_24h": build_heartbeat_bars(await _host_history(host_id, 24), hours=24, buckets=48),
        "bars_7d": build_heartbeat_bars(await _host_history(host_id, 24 * 7, limit=50000), hours=24 * 7, buckets=56),
        "bars_30d": build_heartbeat_bars(
            await _host_history(host_id, 24 * 30, limit=80000),
            hours=24 * 30,
            buckets=60,
        ),
        "uptime_24h": uptime_percent(await _host_history(host_id, 24)),
        "uptime_7d": uptime_percent(await _host_history(host_id, 24 * 7, limit=50000)),
        "uptime_30d": uptime_percent(await _host_history(host_id, 24 * 30, limit=80000)),
    }


async def build_metrics_payload(host_id: int) -> dict[str, Any]:
    rows = await _host_history(host_id, 24)
    primary = pick_primary_service(await db.list_services(host_id))
    primary_id = primary.get("id") if primary else None
    primary_rows = [r for r in rows if primary_id is None or r.get("service_id") == primary_id]

    response_series = [
        {
            "at": row.get("checked_at"),
            "ms": row.get("response_time_ms"),
            "status": row.get("status"),
            "service_id": row.get("service_id"),
            "check_type": row.get("check_type"),
        }
        for row in reversed(primary_rows)
        if row.get("response_time_ms") is not None
    ][-120:]

    status_series = [
        {"at": row.get("checked_at"), "status": _status_bucket(row.get("status"))}
        for row in reversed(rows)
    ][-120:]

    ping_rows = [r for r in rows if str(r.get("check_type") or "").lower() == "ping"]
    packet_loss = None
    if ping_rows:
        failed = sum(1 for r in ping_rows if _status_bucket(r.get("status")) != "ok")
        packet_loss = round(100.0 * failed / len(ping_rows), 2)

    tcp_rows = [r for r in rows if str(r.get("check_type") or "").lower() == "tcp"]
    tcp_response_series = [
        {
            "at": row.get("checked_at"),
            "ms": row.get("response_time_ms"),
            "status": row.get("status"),
            "target": row.get("target"),
        }
        for row in reversed(tcp_rows)
        if row.get("response_time_ms") is not None
    ][-120:]

    return {
        "device_id": f"host-{host_id}",
        "response_series": response_series,
        "status_series": status_series,
        "packet_loss_24h": packet_loss,
        "has_ping": bool(ping_rows),
        "tcp_response_series": tcp_response_series,
        "has_tcp": bool(tcp_rows),
        "tcp_avg_response_24h": average_response_ms(tcp_rows),
    }
