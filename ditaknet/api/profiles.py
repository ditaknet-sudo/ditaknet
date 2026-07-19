"""Device profile API — read-only profile templates."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ditaknet.profiles.device_profiles import list_profiles, profile_detail
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.get("")
async def get_profiles(user: AuthenticatedUser = Depends(require_permissions("read"))):
    return {"profiles": list_profiles()}


@router.get("/{device_type}")
async def get_profile_detail(
    device_type: str,
    lang: str = Query("en"),
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    return profile_detail(device_type, lang)
