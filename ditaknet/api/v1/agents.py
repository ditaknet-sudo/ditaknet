"""
Agent API — registration, heartbeat, metrics ingestion.

Registration requires ``AGENT_REGISTRATION_KEY`` (one-time bootstrap).
Subsequent calls authenticate via hashed token in ``X-Agent-Token`` (configurable).
Missing heartbeats are detected by a scheduler job, not inline on each request.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ditaknet import database as db
from ditaknet.api.agent_auth import get_authenticated_agent, verify_registration_key
from ditaknet.core.agent_tokens import generate_agent_token, hash_agent_token
from ditaknet.core.licensing import LicenseLimitError, license_service
from ditaknet.core.metrics_engine import process_agent_metrics, record_agent_heartbeat
from ditaknet.models import (
    Agent,
    AgentAlert,
    AgentHeartbeat,
    AgentMetric,
    AgentMetricsSubmit,
    AgentRegister,
    AgentRegisterResponse,
)
from ditaknet.security import AuthenticatedUser, require_permissions

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("/register", response_model=AgentRegisterResponse, status_code=201)
async def register_agent(
    payload: AgentRegister,
    _: None = Depends(verify_registration_key),
):
    """Register a new DitakNet agent and return a one-time token."""
    try:
        await license_service.enforce_agent_register()
    except LicenseLimitError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if payload.host_id is not None and not await db.get_host(payload.host_id):
        raise HTTPException(status_code=404, detail="Host not found")

    token = generate_agent_token()
    agent = await db.create_agent(
        name=payload.name.strip(),
        token_hash=hash_agent_token(token),
        hostname=(payload.hostname or "").strip(),
        host_id=payload.host_id,
        status="pending",
    )
    return AgentRegisterResponse(
        agent_id=agent["id"],
        name=agent["name"],
        token=token,
        status=agent["status"],
    )


@router.get("", response_model=list[Agent])
async def list_agents(
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    """List registered agents."""
    return await db.list_agents()


@router.post("/heartbeat")
async def agent_heartbeat(
    payload: AgentHeartbeat,
    agent: dict = Depends(get_authenticated_agent),
):
    """Record agent heartbeat and mark the agent online."""
    updated = await record_agent_heartbeat(agent)
    return {
        "agent_id": updated["id"],
        "status": updated["status"],
        "last_heartbeat_at": updated.get("last_heartbeat_at"),
    }


@router.post("/metrics", response_model=AgentMetric)
async def submit_agent_metrics(
    payload: AgentMetricsSubmit,
    agent: dict = Depends(get_authenticated_agent),
):
    """Submit CPU/RAM/Disk metrics and evaluate threshold rules."""
    results = await process_agent_metrics(
        agent,
        payload.model_dump(),
    )
    return results[0]


@router.get("/{agent_id}", response_model=Agent)
async def get_agent_detail(
    agent_id: int,
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    agent = await db.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.get("/{agent_id}/metrics", response_model=list[AgentMetric])
async def get_agent_metrics(
    agent_id: int,
    limit: int = 100,
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    if not await db.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    return await db.list_agent_metrics(agent_id, limit=limit)


@router.get("/{agent_id}/alerts", response_model=list[AgentAlert])
async def get_agent_alerts(
    agent_id: int,
    active_only: bool = False,
    user: AuthenticatedUser = Depends(require_permissions("read")),
):
    if not await db.get_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    return await db.list_agent_alerts(agent_id=agent_id, active_only=active_only)
