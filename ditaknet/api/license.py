"""Complimentary Professional access API."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ditaknet.core.licensing import license_service
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(tags=["license"])


@router.get("/license")
@router.get("/license/status")
async def license_status(
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    return await license_service.status()


@router.get("/license/scope")
async def license_scope(
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    return await license_service.scope_status()


@router.get("/license/packages")
async def legacy_package_status(
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    """Keep old clients informative without exposing a sales catalog."""
    return {
        "sales_enabled": False,
        "activation_required": False,
        "trial_available": False,
        "distribution": "complimentary_professional",
        "packages": [
            {
                "code": "PROFESSIONAL",
                "included": True,
                "price": 0,
                "expires_at": None,
            }
        ],
    }
