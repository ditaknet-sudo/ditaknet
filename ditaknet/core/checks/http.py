"""
HTTP/HTTPS check via httpx.

Honours per-service ``timeout_seconds`` and optional expected status code.
URLs without a scheme get ``http://`` injected in ``_build_url``.
"""

from __future__ import annotations

import time
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import httpx
from loguru import logger

from ditaknet.core.checks.base import BaseCheck, CheckResponse


class HttpCheck(BaseCheck):
    """HTTP/HTTPS endpoint health check."""

    check_type = "http"

    async def execute(
        self,
        target: str,
        *,
        port: Optional[int] = None,
        timeout: int = 10,
        expected_status_code: Optional[int] = 200,
        method: str = "GET",
        headers: Optional[dict] = None,
        **kwargs,
    ) -> CheckResponse:
        """Send an HTTP request to *target* and validate the response.

        When ``expected_status_code`` is provided, success remains an exact
        status match for backward compatibility. When it is ``None``, any
        non-error HTTP status (2xx or 3xx after redirects) is considered OK.
        """
        url = self._build_url(target, port)

        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout),
                follow_redirects=True,
                verify=False,  # monitoring checks shouldn't fail on self-signed certs
            ) as client:
                response = await client.request(method, url, headers=headers or {})
                elapsed_ms = (time.perf_counter() - start) * 1000

                status_info = self._classify_status(
                    response.status_code,
                    expected_status_code,
                )
                extra = {
                    "status_code": response.status_code,
                    "status_class": status_info["status_class"],
                    "expected_status_code": expected_status_code,
                    "retryable": status_info["retryable"],
                    "redirect_count": len(getattr(response, "history", []) or []),
                }

                if status_info["success"]:
                    return CheckResponse(
                        success=True,
                        response_time_ms=elapsed_ms,
                        message=f"HTTP {method} {url} -> {response.status_code}",
                        extra=extra,
                    )

                expectation = (
                    f"expected {expected_status_code}"
                    if expected_status_code is not None
                    else "expected a non-error HTTP status"
                )
                return CheckResponse(
                    success=False,
                    response_time_ms=elapsed_ms,
                    message=(
                        f"HTTP {method} {url} -> {response.status_code} "
                        f"({status_info['status_class']}, {expectation})"
                    ),
                    extra=extra,
                )

        except httpx.TimeoutException:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return CheckResponse(
                success=False,
                response_time_ms=elapsed_ms,
                message=f"HTTP {method} {url} timed out after {timeout}s",
                extra={"error_type": "timeout", "retryable": True},
            )
        except httpx.ConnectError as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return CheckResponse(
                success=False,
                response_time_ms=elapsed_ms,
                message=f"HTTP connection error: {exc}",
                extra={"error_type": "connect_error", "retryable": True},
            )
        except httpx.InvalidURL as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return CheckResponse(
                success=False,
                response_time_ms=elapsed_ms,
                message=f"HTTP invalid URL: {exc}",
                extra={"error_type": "invalid_url", "retryable": False},
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.warning("HTTP check error for {}: {}", url, exc)
            return CheckResponse(
                success=False,
                response_time_ms=elapsed_ms,
                message=f"HTTP error: {exc}",
                extra={"error_type": exc.__class__.__name__, "retryable": True},
            )

    @staticmethod
    def _build_url(target: str, port: Optional[int]) -> str:
        """Ensure target has a scheme and optionally inject a port."""
        url = target
        if not url.startswith(("http://", "https://")):
            url = f"http://{url}"
        if not port:
            return url

        parts = urlsplit(url)
        try:
            if parts.port is not None:
                return url
        except ValueError:
            return url

        userinfo, separator, _hostport = parts.netloc.rpartition("@")
        auth = f"{userinfo}@" if separator else ""
        host = parts.hostname or _hostport or parts.netloc
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"

        netloc = f"{auth}{host}:{port}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

    @staticmethod
    def _classify_status(
        status_code: int,
        expected_status_code: Optional[int],
    ) -> dict[str, object]:
        """Classify an HTTP response for check success and retry behavior."""
        if 100 <= status_code <= 199:
            status_class = "informational"
        elif 200 <= status_code <= 299:
            status_class = "success"
        elif 300 <= status_code <= 399:
            status_class = "redirect"
        elif 400 <= status_code <= 499:
            status_class = "client_error"
        elif 500 <= status_code <= 599:
            status_class = "server_error"
        else:
            status_class = "unknown"

        if expected_status_code is None:
            success = 200 <= status_code <= 399
        else:
            success = status_code == expected_status_code

        retryable = status_code in {408, 425, 429} or 500 <= status_code <= 599
        if success:
            retryable = False

        return {
            "status_class": status_class,
            "success": success,
            "retryable": retryable,
        }
