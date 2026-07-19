"""
Agent metric thresholds and missing-heartbeat monitoring.

Separate from service check alerts: uses ``agent_alerts`` table and scheduler job
``agent:heartbeat_monitor``. Thresholds come from config env vars so TrueNAS
installs can tune CPU/RAM/disk without code changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from loguru import logger

from ditaknet import database as db
from ditaknet.config import settings


@dataclass(frozen=True)
class MetricRule:
    name: str
    warning: float
    critical: float



def get_metric_rules() -> tuple[MetricRule, ...]:
    return (
        MetricRule("cpu", settings.metric_cpu_warning, settings.metric_cpu_critical),
        MetricRule("memory", settings.metric_memory_warning, settings.metric_memory_critical),
        MetricRule("disk", settings.metric_disk_warning, settings.metric_disk_critical),
    )


def classify_metric(value: float, rule: MetricRule) -> str:
    """Return ok, warning, or critical for a metric value."""
    if value >= rule.critical:
        return "critical"
    if value >= rule.warning:
        return "warning"
    return "ok"


def _metric_field_name(rule_name: str) -> str:
    return f"{rule_name}_percent"


async def process_agent_metrics(agent: dict, metrics: dict[str, float]) -> list[dict]:
    """Store metrics and apply CPU/RAM/Disk threshold rules."""
    stored = await db.insert_agent_metrics(
        agent_id=agent["id"],
        cpu_percent=metrics["cpu_percent"],
        memory_percent=metrics["memory_percent"],
        disk_percent=metrics["disk_percent"],
    )
    alerts_created: list[dict] = []

    for rule in get_metric_rules():
        field = _metric_field_name(rule.name)
        value = float(metrics[field])
        severity = classify_metric(value, rule)
        alert_type = f"metric:{rule.name}"

        if severity == "ok":
            active = await db.get_active_agent_alert(agent["id"], alert_type)
            if active:
                await db.resolve_agent_alert(active["id"])
            continue

        active = await db.get_active_agent_alert(agent["id"], alert_type)
        if active and active["severity"] == severity:
            continue

        if active:
            await db.resolve_agent_alert(active["id"])

        message = (
            f"{agent['name']}: {rule.name.upper()} at {value:.1f}% "
            f"({severity} threshold {rule.critical if severity == 'critical' else rule.warning}%)"
        )
        alert = await db.create_agent_alert(
            agent_id=agent["id"],
            alert_type=alert_type,
            message=message,
            severity=severity,
            metric_name=rule.name,
            metric_value=value,
            threshold=rule.critical if severity == "critical" else rule.warning,
        )
        alerts_created.append(alert)
        logger.warning("Agent metric alert: {}", message)

    if agent.get("status") != "online":
        await db.update_agent(agent["id"], status="online")

    return [stored, *alerts_created]


async def record_agent_heartbeat(agent: dict) -> dict:
    """Update agent heartbeat timestamp and online status."""
    return await db.update_agent(
        agent["id"],
        status="online",
        last_heartbeat_at=datetime.now(UTC).isoformat(),
    )


async def check_missing_heartbeats() -> int:
    """Mark agents offline and raise alerts when heartbeat is missing."""
    cutoff = (
        datetime.now(UTC) - timedelta(seconds=settings.agent_heartbeat_timeout_seconds)
    ).isoformat()
    stale_agents = await db.list_stale_agents(cutoff)
    count = 0

    for agent in stale_agents:
        await db.update_agent(agent["id"], status="offline")
        alert_type = "heartbeat:missing"
        active = await db.get_active_agent_alert(agent["id"], alert_type)
        if not active:
            message = (
                f"{agent['name']}: heartbeat missing for more than "
                f"{settings.agent_heartbeat_timeout_seconds}s"
            )
            await db.create_agent_alert(
                agent_id=agent["id"],
                alert_type=alert_type,
                message=message,
                severity="critical",
                metric_name="heartbeat",
            )
            logger.warning("Agent heartbeat missing: {}", agent["name"])
        count += 1

    for agent in await db.list_agents():
        if not agent.get("enabled"):
            continue
        if agent.get("status") != "online":
            continue
        if not agent.get("last_heartbeat_at"):
            continue
        if agent["last_heartbeat_at"] >= cutoff:
            active = await db.get_active_agent_alert(agent["id"], "heartbeat:missing")
            if active:
                await db.resolve_agent_alert(active["id"])

    return count
