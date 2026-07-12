"""First-run setup API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ditaknet.core.packages import use_cases_payload
from ditaknet.core.setup_state import (
    get_monitoring_use_case,
    get_setup_status,
    save_monitoring_use_case,
)
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/setup", tags=["setup"])


class UseCaseSelect(BaseModel):
    use_case: str = Field(..., min_length=1)


@router.get("/status")
async def setup_status():
    """Public setup status (no secrets)."""
    return await get_setup_status()


@router.get("/use-cases")
async def list_use_cases():
    return {"use_cases": use_cases_payload()}


@router.post("/use-case")
async def select_use_case(payload: UseCaseSelect):
    await save_monitoring_use_case(payload.use_case)
    return {"use_case": await get_monitoring_use_case()}


@router.get("/complete")
async def setup_complete_check(
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    return await get_setup_status()
