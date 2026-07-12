"""
HTTP client for remote DitakNet agents.

Typical loop: register (one-time key) → heartbeat interval → collect_and_submit().
Uses ``settings.agent_token_header`` so deployments can rename the header if needed.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from ditaknet.agent.collector import collect_system_metrics
from ditaknet.config import settings


class AgentClient:
    """Minimal agent client for registration, heartbeat, and metrics."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        registration_key: Optional[str] = None,
        timeout: float = 10.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.registration_key = registration_key
        self.timeout = timeout

    def _agent_headers(self) -> dict[str, str]:
        return {settings.agent_token_header: self.token}

    async def register(
        self,
        name: str,
        *,
        hostname: str = "",
        host_id: Optional[int] = None,
    ) -> dict[str, Any]:
        headers = {}
        if self.registration_key:
            headers["X-Registration-Key"] = self.registration_key
        payload = {"name": name, "hostname": hostname, "host_id": host_id}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/agents/register",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            return response.json()

    async def heartbeat(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/agents/heartbeat",
                json={"status": "online"},
                headers=self._agent_headers(),
            )
            response.raise_for_status()
            return response.json()

    async def submit_metrics(self, metrics: dict[str, float]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/api/v1/agents/metrics",
                json=metrics,
                headers=self._agent_headers(),
            )
            response.raise_for_status()
            return response.json()

    async def collect_and_submit(self, *, disk_path: Optional[str] = None) -> dict[str, Any]:
        metrics = collect_system_metrics(disk_path=disk_path)
        return await self.submit_metrics(metrics)
