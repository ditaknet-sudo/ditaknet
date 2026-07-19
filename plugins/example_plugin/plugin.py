"""
Example Plugin — DNS resolution check.

Demonstrates how to create a DitakNet plugin that adds a custom
check type.  This plugin adds a ``dns`` check type that verifies
a hostname can be resolved to an IP address.
"""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Optional

from loguru import logger

from ditaknet.core.checks.base import BaseCheck, CheckResponse
from ditaknet.plugins.base import BasePlugin


class DnsCheck(BaseCheck):
    """DNS resolution check — verifies that a hostname resolves."""

    check_type = "dns"

    async def execute(
        self,
        target: str,
        *,
        port: Optional[int] = None,
        timeout: int = 10,
        **kwargs,
    ) -> CheckResponse:
        """Resolve *target* hostname and return the result."""
        start = time.perf_counter()
        try:
            loop = asyncio.get_event_loop()
            result = await asyncio.wait_for(
                loop.getaddrinfo(target, port or 80, family=socket.AF_UNSPEC),
                timeout=timeout,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000

            if result:
                ips = list({addr[4][0] for addr in result})
                return CheckResponse(
                    success=True,
                    response_time_ms=elapsed_ms,
                    message=f"DNS resolved {target} → {', '.join(ips)}",
                    extra={"resolved_ips": ips},
                )
            else:
                return CheckResponse(
                    success=False,
                    response_time_ms=elapsed_ms,
                    message=f"DNS resolution returned empty for {target}",
                )

        except asyncio.TimeoutError:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return CheckResponse(
                success=False,
                response_time_ms=elapsed_ms,
                message=f"DNS resolution timed out for {target}",
            )
        except socket.gaierror as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return CheckResponse(
                success=False,
                response_time_ms=elapsed_ms,
                message=f"DNS resolution failed for {target}: {exc}",
            )


class Plugin(BasePlugin):
    """Example DNS check plugin."""

    name = "dns_check"
    version = "1.0.0"
    description = "Adds DNS resolution check type"

    async def on_load(self, app_context: dict) -> None:
        self.register_check("dns", DnsCheck)
        logger.info("DNS check plugin loaded")

    async def on_unload(self) -> None:
        logger.info("DNS check plugin unloaded")
