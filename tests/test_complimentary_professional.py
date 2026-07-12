from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from ditaknet import database as db
from ditaknet.core.features import feature_flags_from_license
from ditaknet.core.licensing import (
    COMPLIMENTARY_DISTRIBUTION,
    PROFESSIONAL_LIMITS,
    LicenseService,
)
from ditaknet.core.packages import PACKAGE_PROFESSIONAL, normalize_tier
from ditaknet.core.setup_state import SETUP_STEPS


def test_all_legacy_tiers_resolve_to_professional() -> None:
    for tier in ("FREE", "TRIAL", "LITE", "MEDIUM", "PRO", "CORPORATE"):
        assert normalize_tier(tier) == PACKAGE_PROFESSIONAL


def test_professional_limits_enable_every_module_without_capacity_limits() -> None:
    limits = PROFESSIONAL_LIMITS
    assert limits.max_hosts is None
    assert limits.max_services is None
    assert limits.max_discovery_subnets is None
    assert limits.max_offices is None
    assert limits.max_branch_agents is None

    flags = feature_flags_from_license(limits.to_dict())
    assert flags
    assert all(flags.values())


def test_setup_has_no_package_or_activation_step() -> None:
    assert "package" not in SETUP_STEPS
    assert "activate" not in SETUP_STEPS
    assert SETUP_STEPS[:3] == ("language", "purpose", "admin")


@pytest.mark.parametrize("legacy_status", ["trial", "expired"])
def test_legacy_license_is_migrated_to_complimentary_professional(
    monkeypatch: pytest.MonkeyPatch,
    legacy_status: str,
) -> None:
    service = LicenseService()
    existing = {
        "id": 12,
        "tier": "MEDIUM",
        "status": legacy_status,
        "expires_at": "2026-01-01T00:00:00+00:00",
        "limits_json": json.dumps({"license_status": "trial"}),
    }
    create_license = AsyncMock(return_value={"id": 13})
    update_status = AsyncMock()
    monkeypatch.setattr(db, "get_active_license", AsyncMock(return_value=existing))
    monkeypatch.setattr(db, "deactivate_all_licenses", AsyncMock())
    monkeypatch.setattr(db, "update_license_status", update_status)
    monkeypatch.setattr(db, "create_license", create_license)
    monkeypatch.setattr(db, "set_app_setting", AsyncMock())

    asyncio.run(service.ensure_default_license())

    created = create_license.await_args.kwargs
    assert created["tier"] == PACKAGE_PROFESSIONAL
    assert created["status"] == "active"
    assert created["expires_at"] is None
    payload = json.loads(created["limits_json"])
    assert payload["distribution"] == COMPLIMENTARY_DISTRIBUTION
    assert payload["activation_required"] is False
    assert payload["trial_available"] is False
    assert payload["complimentary"] is True
    if legacy_status == "expired":
        update_status.assert_awaited_once_with(12, "replaced")


def test_status_is_always_active_professional(monkeypatch: pytest.MonkeyPatch) -> None:
    service = LicenseService()

    async def get_setting(key: str, default: str | None = None) -> str:
        values = {
            "installation_id": "installation-test-id",
            "monitoring_use_case": "business_network",
        }
        return values.get(key, default or "")

    async def count_table(table: str) -> int:
        return {"offices": 2, "branch_agents": 3}.get(table, 0)

    monkeypatch.setattr(db, "list_hosts", AsyncMock(return_value=[{"id": 1}]))
    monkeypatch.setattr(db, "list_services", AsyncMock(return_value=[{"id": 7}]))
    monkeypatch.setattr(db, "get_app_setting", get_setting)
    monkeypatch.setattr(
        service,
        "_used_networks",
        AsyncMock(return_value=["192.168.1.0/24"]),
    )
    monkeypatch.setattr(service, "_count_table", count_table)

    status = asyncio.run(service.status())

    assert status["tier"] == PACKAGE_PROFESSIONAL
    assert status["status"] == "active"
    assert status["license_status"] == "included"
    assert status["expires_at"] is None
    assert status["operational_access"] is True
    assert status["write_allowed"] is True
    assert status["max_hosts"] is None
    assert status["used_hosts"] == 1
    assert status["used_services"] == 1
    assert status["used_subnets"] == 1
    assert status["used_offices"] == 2
    assert status["used_branch_agents"] == 3


def test_openapi_has_no_activation_trial_or_purchase_operations() -> None:
    from ditaknet.main import app

    paths = set(app.openapi()["paths"])
    assert "/api/license/status" in paths
    assert not any("activate" in path or "trial" in path for path in paths)
    assert not any("purchase" in path for path in paths)
