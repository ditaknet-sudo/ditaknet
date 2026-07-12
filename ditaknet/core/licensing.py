"""Complimentary Professional access and compatibility enforcement helpers."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from loguru import logger

from ditaknet import database as db
from ditaknet.core.packages import PACKAGE_PROFESSIONAL

INSTALLATION_ID_KEY = "installation_id"
MONITORING_USE_CASE_KEY = "monitoring_use_case"
COMPLIMENTARY_DISTRIBUTION = "complimentary_professional"


class LicenseError(Exception):
    """Base access-policy error retained for API compatibility."""


class LicenseLimitError(LicenseError):
    """Raised for invalid resource requests, never for commercial limits."""

    def __init__(
        self,
        message: str,
        *,
        error_key: str | None = None,
        **params: Any,
    ) -> None:
        self.error_key = error_key
        self.params = params
        super().__init__(message)


@dataclass(frozen=True)
class TierLimits:
    tier: str
    max_hosts: int | None
    max_services: int | None
    max_discovery_subnets: int | None
    max_vlans_or_networks: int | None
    max_scan_prefix: int
    agent_enabled: bool
    reports_enabled: str
    topology_enabled: str
    bulk_operations_enabled: str
    support_level: str = "community"
    multi_client_or_branch_ready: bool = True
    allowed_network_scope: str = "configurable private networks"
    bulk_max_devices: int | None = None
    audit_logs_enabled: bool = True
    employee_presence_enabled: bool = True
    multi_office_enabled: bool = True
    branch_agent_enabled: bool = True
    max_offices: int | None = None
    max_branch_agents: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "max_hosts": self.max_hosts,
            "max_services": self.max_services,
            "max_discovery_subnets": self.max_discovery_subnets,
            "max_vlans_or_networks": self.max_vlans_or_networks,
            "max_scan_prefix": self.max_scan_prefix,
            "max_scan_size": "configurable",
            "agent_enabled": self.agent_enabled,
            "reports_enabled": self.reports_enabled,
            "topology_enabled": self.topology_enabled,
            "bulk_operations_enabled": self.bulk_operations_enabled,
            "support_level": self.support_level,
            "multi_client_or_branch_ready": self.multi_client_or_branch_ready,
            "allowed_network_scope": self.allowed_network_scope,
            "bulk_max_devices": self.bulk_max_devices,
            "audit_logs_enabled": self.audit_logs_enabled,
            "employee_presence_enabled": self.employee_presence_enabled,
            "multi_office_enabled": self.multi_office_enabled,
            "branch_agent_enabled": self.branch_agent_enabled,
            "max_offices": self.max_offices,
            "max_branch_agents": self.max_branch_agents,
        }


PROFESSIONAL_LIMITS = TierLimits(
    tier=PACKAGE_PROFESSIONAL,
    max_hosts=None,
    max_services=None,
    max_discovery_subnets=None,
    max_vlans_or_networks=None,
    max_scan_prefix=0,
    agent_enabled=True,
    reports_enabled="advanced",
    topology_enabled="advanced",
    bulk_operations_enabled="true",
)

LICENSE_TIERS: dict[str, TierLimits] = {
    PACKAGE_PROFESSIONAL: PROFESSIONAL_LIMITS,
}


def hash_secret(raw_value: str) -> str:
    """Return a stable one-way hash for locally stored access tokens."""
    return hashlib.sha256(raw_value.strip().encode("utf-8")).hexdigest()


def _entitlement_payload() -> dict[str, Any]:
    payload = PROFESSIONAL_LIMITS.to_dict()
    payload.update(
        {
            "distribution": COMPLIMENTARY_DISTRIBUTION,
            "package_code": PACKAGE_PROFESSIONAL,
            "license_status": "included",
            "payment_status": "not_required",
            "activation_required": False,
            "trial_available": False,
            "complimentary": True,
        }
    )
    return payload


class LicenseService:
    """Expose one permanent Professional entitlement for every installation."""

    async def ensure_default_license(self) -> None:
        """Migrate legacy Free, trial, and expired rows to Professional access."""
        existing = await db.get_active_license()
        existing_limits: dict[str, Any] = {}
        if existing:
            try:
                existing_limits = json.loads(existing.get("limits_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                existing_limits = {}

        already_current = bool(
            existing
            and str(existing.get("tier")) == PACKAGE_PROFESSIONAL
            and str(existing.get("status")) == "active"
            and not existing.get("expires_at")
            and existing_limits.get("distribution") == COMPLIMENTARY_DISTRIBUTION
        )
        if not already_current:
            await db.deactivate_all_licenses()
            if existing and str(existing.get("status")) == "expired":
                await db.update_license_status(int(existing["id"]), "replaced")
            await db.create_license(
                license_key_hash="",
                tier=PACKAGE_PROFESSIONAL,
                owner_name="",
                expires_at=None,
                status="active",
                limits_json=json.dumps(_entitlement_payload()),
                signature_valid=1,
            )
            logger.info("Enabled complimentary Professional access")

        await db.set_app_setting("setup_license_complete", "1")
        await db.set_app_setting("setup_selected_package", PACKAGE_PROFESSIONAL)
        await db.set_app_setting("setup_activation_status", "included")
        await db.set_app_setting("setup_skip_activate", "1")

    async def get_limits(self) -> TierLimits:
        return PROFESSIONAL_LIMITS

    async def _effective_license(self) -> dict[str, Any]:
        row = await db.get_active_license() or {}
        return {
            **row,
            "tier": PACKAGE_PROFESSIONAL,
            "status": "active",
            "expires_at": None,
            "limits_json": json.dumps(_entitlement_payload()),
        }

    async def is_operational_access_allowed(self) -> bool:
        return True

    async def enforce_operational_access(self, module: str = "") -> None:
        return None

    async def is_write_allowed(self) -> bool:
        return True

    async def get_installation_id(self) -> str:
        existing = await db.get_app_setting(INSTALLATION_ID_KEY)
        if existing:
            return existing
        value = str(uuid.uuid4())
        await db.set_app_setting(INSTALLATION_ID_KEY, value)
        return value

    async def _used_networks(self) -> list[str]:
        from ditaknet.discovery.subnet import normalize_subnets, private_ip_network

        found: set[str] = set()
        for scan in await db.list_discovery_scans(limit=1000):
            try:
                subnets = json.loads(scan.get("subnets_json") or "[]")
                found.update(normalize_subnets(subnets))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue

        for host in await db.list_hosts():
            cidr = private_ip_network(str(host.get("address") or ""))
            if cidr:
                found.add(cidr)
        return sorted(found)

    async def _count_table(self, table: str) -> int:
        if table not in {"offices", "branch_agents"}:
            return 0
        try:
            connection = await db.get_db()
            rows = await connection.execute_fetchall(
                f"SELECT COUNT(*) AS count FROM {table}"
            )
            return int(rows[0]["count"]) if rows else 0
        except Exception:
            return 0

    async def scope_status(self) -> dict[str, Any]:
        networks = await self._used_networks()
        host_count = len(await db.list_hosts())
        return {
            "tier": PACKAGE_PROFESSIONAL,
            "allowed_network": "",
            "allowed_free_network_cidr": "",
            "allowed_network_scope": PROFESSIONAL_LIMITS.allowed_network_scope,
            "used_hosts": host_count,
            "max_hosts": None,
            "used_subnets": len(networks),
            "max_subnets": None,
            "max_discovery_subnets": None,
            "max_vlans_or_networks": None,
            "networks": networks,
        }

    async def status(self) -> dict[str, Any]:
        hosts = await db.list_hosts()
        services = await db.list_services()
        networks = await self._used_networks()
        return {
            **_entitlement_payload(),
            "tier": PACKAGE_PROFESSIONAL,
            "package_code": PACKAGE_PROFESSIONAL,
            "license_status": "included",
            "status": "active",
            "expires_at": None,
            "operational_access": True,
            "write_allowed": True,
            "owner_name": "",
            "installation_id": await self.get_installation_id(),
            "used_hosts": len(hosts),
            "used_services": len(services),
            "used_subnets": len(networks),
            "used_offices": await self._count_table("offices"),
            "used_branch_agents": await self._count_table("branch_agents"),
            "monitoring_use_case": (
                await db.get_app_setting(MONITORING_USE_CASE_KEY, "") or ""
            ),
        }

    async def enforce_host_create(
        self,
        additional: int = 1,
        address: str | None = None,
    ) -> None:
        return None

    async def enforce_host_network_scope(self, address: str) -> None:
        return None

    async def enforce_service_create(self, additional: int = 1) -> None:
        return None

    async def enforce_agent_register(self) -> None:
        return None

    async def enforce_discovery_scan(self, subnets: list[str]) -> None:
        from ditaknet.discovery.subnet import limit_subnets_for_scan

        if not limit_subnets_for_scan(subnets, None):
            raise LicenseLimitError("At least one private subnet is required for discovery.")

    async def enforce_bulk_operation(self, device_count: int) -> None:
        return None

    async def enforce_reports_access(self, export: bool = False) -> None:
        return None

    async def enforce_audit_logs_access(self) -> None:
        return None

    async def enforce_employee_presence_access(self) -> None:
        return None

    async def enforce_multi_office_access(self) -> None:
        return None

    async def enforce_office_create(self) -> None:
        return None


license_service = LicenseService()
