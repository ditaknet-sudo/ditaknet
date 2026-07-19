"""
Unified device inventory API.

A *device* in the UI/API is either a monitored host (with service checks) or a
standalone agent (metrics-only). Hosts carry location/tags; services carry check config.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ditaknet import database as db
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("", response_model=dict)
async def list_devices(
    user: AuthenticatedUser = Depends(require_permissions("read")),
) -> dict:
    """List monitored hosts and standalone agents in one inventory view."""
    return await db.get_device_inventory()
