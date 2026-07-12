"""Agent API authentication — registration key + per-agent bearer token."""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from ditaknet import database as db
from ditaknet.config import settings
from ditaknet.core.agent_tokens import hash_agent_token


async def get_authenticated_agent(
    x_agent_token: str | None = Header(None, alias="X-Agent-Token"),
) -> dict:
    """Resolve agent from header token.

    Plain token is only returned once at registration; DB stores SHA-256 hash only.
    """
    if not x_agent_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Agent token required",
        )

    agent = await db.get_agent_by_token_hash(hash_agent_token(x_agent_token))
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid agent token",
        )
    return agent


def verify_registration_key(
    x_registration_key: str | None = Header(None, alias="X-Registration-Key"),
) -> None:
    """Validate the shared agent registration key."""
    expected = settings.agent_registration_key or ""
    provided = x_registration_key or ""
    if not expected or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid registration key",
        )
