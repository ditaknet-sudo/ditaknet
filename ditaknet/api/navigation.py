"""Navigation status API for sidebar badges."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ditaknet.core.navigation_status import build_navigation_status
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/navigation", tags=["navigation"])


@router.get("/status")
async def navigation_status(
    user: AuthenticatedUser = Depends(require_permissions("read")),
) -> dict:
    """Safe sidebar status summary (no secrets)."""
    return await build_navigation_status(user)
