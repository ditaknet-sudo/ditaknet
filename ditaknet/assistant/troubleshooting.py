"""
Troubleshooting analysis for devices and alerts.

Combines check results, profile hints, and static rules into suggested next steps.
"""

from __future__ import annotations

from typing import Any

from ditaknet.assistant.rules import (
    GENERIC_CRITICAL_RULES,
    HTTP_FAIL_PING_OK_RULES,
    PING_FAIL_RULES,
    TCP_554_FAIL_RULES,
    AssistantRule,
)
from ditaknet.i18n import translate
from ditaknet.profiles.device_profiles import normalize_device_type
from ditaknet.profiles.troubleshooting import profile_troubleshoot_hint


def _rules_to_suggestions(rules: list[AssistantRule], lang: str) -> list[dict[str, Any]]:
    items = sorted(rules, key=lambda r: -r.priority)
    return [
        {"rule_id": r.rule_id, "message": translate(r.message_key, lang), "priority": r.priority}
        for r in items
    ]


def _service_context(services: list[dict]) -> dict[str, Any]:
    ping_ok = any(s.get("current_state") == "ok" for s in services if s.get("check_type") == "ping")
    ping_fail = any(s.get("current_state") in ("warning", "critical") for s in services if s.get("check_type") == "ping")
    http_fail = any(
        s.get("current_state") in ("warning", "critical") for s in services if s.get("check_type") == "http"
    )
    rtsp_fail = any(
        s.get("current_state") in ("warning", "critical")
        for s in services
        if s.get("check_type") == "tcp" and s.get("port") == 554
    )
    return {"ping_ok": ping_ok, "ping_fail": ping_fail, "http_fail": http_fail, "rtsp_fail": rtsp_fail}


async def analyze_device(host: dict, services: list[dict], lang: str = "en") -> dict[str, Any]:
    """Return troubleshooting suggestions for a host/device."""
    ctx = _service_context(services)
    device_type = normalize_device_type(host.get("host_type") or "unknown")
    suggestions: list[dict[str, Any]] = []

    if ctx["ping_fail"] or not services:
        suggestions.extend(_rules_to_suggestions(PING_FAIL_RULES, lang))
    elif ctx["http_fail"] and ctx["ping_ok"]:
        suggestions.extend(_rules_to_suggestions(HTTP_FAIL_PING_OK_RULES, lang))
    elif ctx["rtsp_fail"]:
        suggestions.extend(_rules_to_suggestions(TCP_554_FAIL_RULES, lang))
    elif any(s.get("current_state") in ("warning", "critical") for s in services):
        suggestions.extend(_rules_to_suggestions(GENERIC_CRITICAL_RULES, lang))

    profile_hint = profile_troubleshoot_hint(device_type, lang)
    return {
        "device_id": host["id"],
        "device_type": device_type,
        "profile_hint": profile_hint,
        "suggested_actions": suggestions[:8],
    }


async def analyze_alert(
    alert: dict,
    service: dict | None,
    host: dict | None,
    all_services: list[dict],
    lang: str = "en",
) -> dict[str, Any]:
    """Return suggestions tied to a specific alert."""
    base = {"alert_id": alert["id"], "severity": alert.get("severity"), "suggested_actions": []}
    if host:
        device_analysis = await analyze_device(host, all_services, lang)
        base["profile_hint"] = device_analysis.get("profile_hint")
        base["suggested_actions"] = device_analysis.get("suggested_actions", [])

    if service:
        ctype = service.get("check_type", "")
        if ctype == "ping":
            base["suggested_actions"] = _rules_to_suggestions(PING_FAIL_RULES, lang)
        elif ctype == "http":
            base["suggested_actions"] = _rules_to_suggestions(HTTP_FAIL_PING_OK_RULES, lang)
        elif ctype == "tcp" and service.get("port") == 554:
            base["suggested_actions"] = _rules_to_suggestions(TCP_554_FAIL_RULES, lang)

    return base
