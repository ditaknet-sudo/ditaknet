"""Logical network topology API (grouped views, not graph layout)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ditaknet import database as db
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/topology", tags=["topology"])


@router.get("")
async def get_topology(user: AuthenticatedUser = Depends(require_permissions("read"))):
    return await db.get_topology()
