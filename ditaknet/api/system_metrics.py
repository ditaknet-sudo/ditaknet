"""Server host metrics API."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ditaknet.core.system_metrics import collect_system_metrics
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/system/metrics", tags=["system-metrics"])


@router.get("")
async def system_metrics(
    user: AuthenticatedUser = Depends(require_permissions("system.activity.view")),
) -> dict:
    metrics = collect_system_metrics()
    return metrics
