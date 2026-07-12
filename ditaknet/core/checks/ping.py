"""
ICMP ping check via system ``ping`` subprocess.

Uses OS ping instead of raw ICMP so the app can run unprivileged in Docker/TrueNAS.
Latency is parsed from locale-specific output; see ``_LATENCY_PATTERNS``.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import platform
import re
import time
from typing import Optional

from loguru import logger

from ditaknet.core.checks.base import BaseCheck, CheckResponse


class PingCheck(BaseCheck):
    """ICMP ping check using the system ping command."""

    check_type = "ping"

    # Regex patterns to extract round-trip time from ping output
    _LATENCY_PATTERNS = [
        # Linux / macOS: "time=12.3 ms"
        re.compile(r"time[=<]([\d.]+)\s*ms", re.IGNORECASE),
        # Windows: "time=12ms" or "time<1ms"
        re.compile(r"time[=<]([\d.]+)\s*ms", re.IGNORECASE),
        # Windows summary: "Average = 12ms"
        re.compile(r"average\s*=\s*([\d.]+)\s*ms", re.IGNORECASE),
        # macOS summary: "round-trip min/avg/max/stddev = 1.0/2.0/3.0/0.1 ms"
        re.compile(r"round-trip.*=\s*[\d.]+/([\d.]+)/", re.IGNORECASE),
    ]

    _FAILURE_PATTERNS = [
        re.compile(r"100(?:\.0)?%\s*packet loss", re.IGNORECASE),
        re.compile(r"\(100(?:\.0)?%\s*loss\)", re.IGNORECASE),
        re.compile(r"request timed out", re.IGNORECASE),
        re.compile(r"destination .*unreachable", re.IGNORECASE),
        re.compile(r"could not find host", re.IGNORECASE),
        re.compile(r"unknown host", re.IGNORECASE),
        re.compile(r"name or service not known", re.IGNORECASE),
    ]

    async def execute(
        self,
        target: str,
        *,
        port: Optional[int] = None,
        timeout: int = 10,
        **kwargs,
    ) -> CheckResponse:
        """Ping *target* and return latency + reachability."""
        timeout = self._normalise_timeout(timeout)
        cmd = self._build_command(target, timeout)
        process_timeout = timeout + 1.0

        start = time.perf_counter()
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=process_timeout
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            output = self._decode_output(stdout, stderr)

            if proc.returncode == 0 and not self._has_failure_output(output):
                latency = self._parse_latency(output)
                return CheckResponse(
                    success=True,
                    response_time_ms=latency if latency is not None else elapsed_ms,
                    message=f"Ping to {target} succeeded",
                    extra={
                        "exit_code": proc.returncode,
                        "retryable": False,
                    },
                )

            reason = self._failure_reason(output) or f"exit code {proc.returncode}"
            return CheckResponse(
                success=False,
                response_time_ms=elapsed_ms,
                message=f"Ping to {target} failed ({reason})",
                extra={
                    "exit_code": proc.returncode,
                    "retryable": True,
                },
            )

        except asyncio.TimeoutError:
            elapsed_ms = (time.perf_counter() - start) * 1000
            if proc is not None:
                try:
                    maybe_awaitable = proc.kill()
                    if inspect.isawaitable(maybe_awaitable):
                        await maybe_awaitable
                    await asyncio.wait_for(proc.communicate(), timeout=0.2)
                except Exception:
                    pass
            return CheckResponse(
                success=False,
                response_time_ms=elapsed_ms,
                message=f"Ping to {target} timed out after {timeout}s",
                extra={"error_type": "timeout", "retryable": True},
            )
        except FileNotFoundError:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return CheckResponse(
                success=False,
                response_time_ms=elapsed_ms,
                message="Ping command not found",
                extra={"error_type": "missing_ping", "retryable": False},
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.warning("Ping check error for {}: {}", target, exc)
            return CheckResponse(
                success=False,
                response_time_ms=elapsed_ms,
                message=f"Ping error: {exc}",
                extra={"error_type": exc.__class__.__name__, "retryable": True},
            )

    def _build_command(self, target: str, timeout: float) -> list[str]:
        system = platform.system().lower()
        if system == "windows":
            return ["ping", "-n", "1", "-w", str(math.ceil(timeout * 1000)), target]
        if system == "darwin":
            return ["ping", "-c", "1", "-W", str(math.ceil(timeout * 1000)), target]
        return ["ping", "-c", "1", "-W", str(math.ceil(timeout)), target]

    def _parse_latency(self, output: str) -> Optional[float]:
        """Extract round-trip time in ms from ping output."""
        for pattern in self._LATENCY_PATTERNS:
            match = pattern.search(output)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    continue
        return None

    @classmethod
    def _has_failure_output(cls, output: str) -> bool:
        return any(pattern.search(output) for pattern in cls._FAILURE_PATTERNS)

    @classmethod
    def _failure_reason(cls, output: str) -> Optional[str]:
        for line in output.splitlines():
            if cls._has_failure_output(line):
                return line.strip()
        return None

    @staticmethod
    def _decode_output(stdout: bytes, stderr: bytes) -> str:
        return "\n".join(
            part.decode(errors="replace").strip()
            for part in (stdout, stderr)
            if part
        )

    @staticmethod
    def _normalise_timeout(timeout: int | float) -> float:
        return max(float(timeout), 0.1)
