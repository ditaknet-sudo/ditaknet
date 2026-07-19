"""
TCP port reachability check.

Requires ``port`` on the service row; connect timeout matches service config.
A refused connection is a definitive failure (not retryable as network blip).
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from loguru import logger

from ditaknet.core.checks.base import BaseCheck, CheckResponse


class TcpCheck(BaseCheck):
    """TCP port connectivity check."""

    check_type = "tcp"

    async def execute(
        self,
        target: str,
        *,
        port: Optional[int] = None,
        timeout: int = 10,
        **kwargs,
    ) -> CheckResponse:
        """Attempt a TCP connection to *target*:*port*.

        Parameters
        ----------
        target:
            Hostname or IP address.
        port:
            TCP port number (required for TCP checks).
        """
        if port is None:
            return CheckResponse(
                success=False,
                message="TCP check requires a port number",
                extra={"error_type": "missing_port", "retryable": False},
            )

        timeout = self._normalise_timeout(timeout)
        start = time.perf_counter()
        writer: asyncio.StreamWriter | None = None
        try:
            async with asyncio.timeout(timeout):
                _reader, writer = await asyncio.open_connection(target, port)
            elapsed_ms = (time.perf_counter() - start) * 1000

            return CheckResponse(
                success=True,
                response_time_ms=elapsed_ms,
                message=f"TCP connect to {target}:{port} succeeded",
                extra={"retryable": False},
            )

        except TimeoutError:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return CheckResponse(
                success=False,
                response_time_ms=elapsed_ms,
                message=f"TCP connect to {target}:{port} timed out after {timeout}s",
                extra={"error_type": "timeout", "retryable": True},
            )
        except ConnectionRefusedError:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return CheckResponse(
                success=False,
                response_time_ms=elapsed_ms,
                message=f"TCP connect to {target}:{port} refused",
                extra={"error_type": "connection_refused", "retryable": False},
            )
        except OSError as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return CheckResponse(
                success=False,
                response_time_ms=elapsed_ms,
                message=f"TCP connect to {target}:{port} failed: {exc}",
                extra={"error_type": exc.__class__.__name__, "retryable": True},
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.warning("TCP check error for {}:{}: {}", target, port, exc)
            return CheckResponse(
                success=False,
                response_time_ms=elapsed_ms,
                message=f"TCP error: {exc}",
                extra={"error_type": exc.__class__.__name__, "retryable": True},
            )
        finally:
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
                except Exception:
                    pass

    @staticmethod
    def _normalise_timeout(timeout: int | float) -> float:
        return max(float(timeout), 0.1)
