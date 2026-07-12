"""
Assistant rule definitions.

Each rule matches failure context and returns i18n message keys — no shell/SSH actions.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AssistantRule:
    rule_id: str
    message_key: str
    priority: int = 50


PING_FAIL_RULES: list[AssistantRule] = [
    AssistantRule("ping_power", "assistant.ping.power_off", 90),
    AssistantRule("ping_cable", "assistant.ping.cable", 85),
    AssistantRule("ping_ip_changed", "assistant.ping.ip_changed", 80),
    AssistantRule("ping_switch", "assistant.ping.switch_router", 75),
    AssistantRule("ping_firewall", "assistant.ping.firewall_icmp", 70),
]

HTTP_FAIL_PING_OK_RULES: list[AssistantRule] = [
    AssistantRule("http_service_down", "assistant.http.service_down", 90),
    AssistantRule("http_wrong_port", "assistant.http.wrong_port", 85),
    AssistantRule("http_ssl", "assistant.http.ssl_issue", 80),
    AssistantRule("http_config", "assistant.http.misconfiguration", 75),
]

TCP_554_FAIL_RULES: list[AssistantRule] = [
    AssistantRule("rtsp_offline", "assistant.rtsp.camera_offline", 90),
    AssistantRule("rtsp_disabled", "assistant.rtsp.disabled", 85),
    AssistantRule("rtsp_vlan", "assistant.rtsp.vlan", 80),
    AssistantRule("rtsp_nvr", "assistant.rtsp.nvr", 75),
]

GENERIC_CRITICAL_RULES: list[AssistantRule] = [
    AssistantRule("verify_power", "assistant.generic.verify_power", 60),
    AssistantRule("check_uplink", "assistant.generic.check_uplink", 55),
    AssistantRule("review_recent_changes", "assistant.generic.recent_changes", 50),
]
