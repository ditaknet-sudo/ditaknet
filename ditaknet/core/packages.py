"""Monitoring use cases for the complimentary Professional edition."""

from __future__ import annotations

from typing import Any

# Legacy names remain import-compatible while every installation resolves to
# the one complimentary Professional edition.
PACKAGE_FREE = "FREE"
PACKAGE_MEDIUM = "MEDIUM"
PACKAGE_PROFESSIONAL = "PROFESSIONAL"


def normalize_tier(tier: str) -> str:
    return PACKAGE_PROFESSIONAL


def public_package_name(tier: str) -> str:
    return PACKAGE_PROFESSIONAL


def tier_at_least(current: str, minimum: str) -> bool:
    return True


MONITORING_USE_CASES: list[dict[str, str]] = [
    {
        "id": "home_small_office",
        "title_key": "usecase.home.title",
        "description_key": "usecase.home.description",
    },
    {
        "id": "cctv_camera",
        "title_key": "usecase.cctv.title",
        "description_key": "usecase.cctv.description",
    },
    {
        "id": "servers_websites",
        "title_key": "usecase.servers.title",
        "description_key": "usecase.servers.description",
    },
    {
        "id": "business_network",
        "title_key": "usecase.business.title",
        "description_key": "usecase.business.description",
    },
    {
        "id": "professional_it",
        "title_key": "usecase.professional.title",
        "description_key": "usecase.professional.description",
    },
    {
        "id": "corporate_attendance",
        "title_key": "usecase.corporate.title",
        "description_key": "usecase.corporate.description",
    },
    {
        "id": "multi_office_branches",
        "title_key": "usecase.multi_office.title",
        "description_key": "usecase.multi_office.description",
    },
]

USE_CASE_IDS = {item["id"] for item in MONITORING_USE_CASES}


def recommended_tier_for_use_case(use_case_id: str) -> str:
    return PACKAGE_PROFESSIONAL


def use_cases_payload() -> list[dict[str, Any]]:
    return [
        {
            **item,
            "recommended_tier": PACKAGE_PROFESSIONAL,
        }
        for item in MONITORING_USE_CASES
    ]
