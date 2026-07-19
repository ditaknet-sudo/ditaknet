"""
Dashboard suggested actions — first-configuration next steps only.

Surfaces up to five priority actions based on real setup state. Hides completed
steps and never promotes demo/stale/unreviewed discovery imports.
"""

from __future__ import annotations

from typing import Any

from ditaknet import database as db
from ditaknet.core.backup import list_backups
from ditaknet.core.licensing import license_service
from ditaknet.core.runtime_settings import telegram_enabled
from ditaknet.discovery.store import list_discovery_inventory, list_monitored_networks
from ditaknet.i18n import translate

MAX_SUGGESTED_ACTIONS = 5

_IMPORTABLE_STATES = frozenset({"new", "active", "seen"})


async def _dashboard_setup_state() -> dict[str, Any]:
    """Collect booleans used to decide which next steps still apply."""
    setup_complete = await db.is_setup_complete()
    monitored = await list_monitored_networks(enabled_only=True)
    monitored_subnets = [str(n.get("cidr") or "").strip() for n in monitored if n.get("cidr")]
    has_monitored_network = bool(monitored_subnets)

    scans = await db.list_discovery_scans(limit=30)
    completed_scans = [
        s
        for s in scans
        if str(s.get("status") or "").lower() == "completed"
        and int(s.get("found_count") or 0) > 0
    ]
    has_completed_scan = bool(completed_scans)

    importable_devices: list[dict[str, Any]] = []
    if has_monitored_network and has_completed_scan:
        inventory = await list_discovery_inventory(
            subnets=monitored_subnets,
            limit=500,
            hide_demo=True,
        )
        importable_devices = [
            d
            for d in inventory
            if str(d.get("device_state") or "") in _IMPORTABLE_STATES
            and not d.get("imported_host_id")
            and not int(d.get("ignored") or 0)
        ]

    hosts = await db.list_hosts()
    services = await db.list_services()
    host_count = len(hosts)
    service_count = len(services)
    hosts_with_checks = len({s.get("host_id") for s in services if s.get("host_id")})

    alerts_configured = bool(await db.get_app_setting("alert_email")) or bool(
        await db.get_app_setting("telegram_bot_token")
    )
    telegram_on = await telegram_enabled()

    backups = list_backups()
    has_backup = len(backups) > 0

    license_status = await license_service.status()
    tier = str(license_status.get("tier") or "FREE").upper()

    return {
        "setup_complete": setup_complete,
        "has_monitored_network": has_monitored_network,
        "has_completed_scan": has_completed_scan,
        "importable_device_count": len(importable_devices),
        "host_count": host_count,
        "service_count": service_count,
        "hosts_with_checks": hosts_with_checks,
        "alerts_configured": alerts_configured,
        "telegram_on": telegram_on,
        "has_backup": has_backup,
        "license_tier": tier,
        "license_near_limit": _license_near_limit(license_status),
    }


def _license_near_limit(license_status: dict) -> bool:
    raw_max = license_status.get("max_hosts")
    try:
        max_hosts = int(raw_max) if raw_max not in (None, "") else None
    except (TypeError, ValueError):
        return False
    if not max_hosts or max_hosts <= 0:
        return False
    used = int(license_status.get("used_hosts") or 0)
    return used >= max_hosts * 0.8


def _action(
    *,
    action_id: str,
    title_key: str,
    desc_key: str,
    href: str,
    badge: str,
    priority: int,
    lang: str,
) -> dict[str, Any]:
    return {
        "action_id": action_id,
        "title": translate(title_key, lang),
        "description": translate(desc_key, lang),
        "href": href,
        "badge": badge,
        "badge_label": translate(f"dashboard.action.badge.{badge}", lang),
        "priority": priority,
    }


async def suggested_dashboard_actions(lang: str = "en") -> list[dict[str, Any]]:
    """Return up to five incomplete setup actions sorted by priority."""
    state = await _dashboard_setup_state()
    actions: list[dict[str, Any]] = []

    if not state["setup_complete"]:
        actions.append(
            _action(
                action_id="complete_setup",
                title_key="dashboard.action.complete_setup.title",
                desc_key="dashboard.action.complete_setup.desc",
                href="/setup",
                badge="required",
                priority=100,
                lang=lang,
            )
        )

    if state["setup_complete"] and not state["has_monitored_network"]:
        actions.append(
            _action(
                action_id="add_subnet",
                title_key="dashboard.action.add_subnet.title",
                desc_key="dashboard.action.add_subnet.desc",
                href="/discovery?tab=networks",
                badge="required",
                priority=95,
                lang=lang,
            )
        )

    if state["setup_complete"] and state["has_monitored_network"] and not state["has_completed_scan"]:
        actions.append(
            _action(
                action_id="run_scan",
                title_key="dashboard.action.run_scan.title",
                desc_key="dashboard.action.run_scan.desc",
                href="/discovery?tab=networks",
                badge="required",
                priority=90,
                lang=lang,
            )
        )

    if (
        state["setup_complete"]
        and state["has_monitored_network"]
        and state["has_completed_scan"]
        and state["importable_device_count"] > 0
    ):
        actions.append(
            _action(
                action_id="import_devices",
                title_key="dashboard.action.import_devices.title",
                desc_key="dashboard.action.import_devices.desc",
                href="/discovery?tab=results",
                badge="recommended",
                priority=85,
                lang=lang,
            )
        )

    if state["setup_complete"] and state["host_count"] > 0 and (
        state["service_count"] == 0 or state["hosts_with_checks"] < state["host_count"]
    ):
        actions.append(
            _action(
                action_id="service_checks",
                title_key="dashboard.action.service_checks.title",
                desc_key="dashboard.action.service_checks.desc",
                href="/services",
                badge="recommended",
                priority=80,
                lang=lang,
            )
        )

    if state["setup_complete"] and state["host_count"] > 0 and not state["has_backup"]:
        actions.append(
            _action(
                action_id="create_backup",
                title_key="dashboard.action.backup.title",
                desc_key="dashboard.action.backup.desc",
                href="/settings/backups",
                badge="recommended",
                priority=70,
                lang=lang,
            )
        )

    if (
        state["setup_complete"]
        and state["host_count"] > 0
        and not state["telegram_on"]
        and not state["alerts_configured"]
    ):
        actions.append(
            _action(
                action_id="telegram",
                title_key="dashboard.action.telegram.title",
                desc_key="dashboard.action.telegram.desc",
                href="/settings/telegram",
                badge="optional",
                priority=55,
                lang=lang,
            )
        )

    if state["setup_complete"] and (
        state["license_tier"] == "FREE" or state["license_near_limit"]
    ):
        actions.append(
            _action(
                action_id="license",
                title_key="dashboard.action.license.title",
                desc_key="dashboard.action.license.desc",
                href="/license",
                badge="optional",
                priority=50,
                lang=lang,
            )
        )

    actions.sort(key=lambda item: -int(item["priority"]))
    return actions[:MAX_SUGGESTED_ACTIONS]


async def maintenance_recommendation_for_alert(
    alert: dict,
    host: dict | None,
    service: dict | None,
    lang: str = "en",
) -> str:
    """Build human-readable maintenance task text from alert context."""
    device_type = (host or {}).get("host_type", "unknown")
    check = (service or {}).get("check_type", "")
    if device_type in ("camera", "nvr") or (check == "tcp" and (service or {}).get("port") == 554):
        return translate("maintenance.rec.poe_cameras", lang)
    if device_type == "router":
        return translate("maintenance.rec.router_uplink", lang)
    if device_type == "nas":
        return translate("maintenance.rec.nas_disk", lang)
    if check == "http":
        return translate("maintenance.rec.restart_web", lang)
    return translate("maintenance.rec.generic", lang)
