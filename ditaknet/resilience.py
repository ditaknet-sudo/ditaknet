"""
Runtime resilience helpers — keep DitakNet serving under partial failures.

- Request ID on every request (header + logs + error JSON).
- Global FastAPI exception handlers return safe JSON instead of leaking traces.
- Asyncio loop handler logs unhandled background-task exceptions.
"""

from __future__ import annotations

import asyncio
import traceback
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException

from ditaknet.config import settings
from ditaknet.core.system_log_service import redact_text

REQUEST_ID_HEADER = "X-Request-ID"


def get_request_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", None)
    if rid:
        return str(rid)
    return request.headers.get(REQUEST_ID_HEADER) or f"req_{uuid.uuid4().hex[:12]}"


def _wants_html(request: Request) -> bool:
    accept = (request.headers.get("accept") or "").lower()
    if "text/html" not in accept:
        return False
    parts = [part.strip().split(";")[0] for part in accept.split(",")]
    return parts and parts[0] == "text/html"


def _user_label(request: Request) -> str | None:
    try:
        session = request.session
    except AssertionError:
        return None
    except Exception:
        return None
    try:
        user = session.get("user")
        if not user:
            return None
        if isinstance(user, dict):
            username = user.get("username")
            return str(username) if username else None
        return str(user)
    except Exception:
        return None


def _safe_exception_location(exc: BaseException) -> str | None:
    tb = traceback.extract_tb(exc.__traceback__)
    if not tb:
        return None
    last = tb[-1]
    return f"{last.filename}:{last.lineno} in {last.name}"


def log_route_exception(request: Request, exc: Exception, *, status_code: int = 500) -> str:
    """Log sanitized traceback; return request_id."""
    request_id = get_request_id(request)
    user = _user_label(request)
    safe_message = redact_text(str(exc))
    logger.bind(
        request_id=request_id,
        path=request.url.path,
        method=request.method,
        user=user or "anonymous",
        status_code=status_code,
        exception_type=type(exc).__name__,
    ).exception(
        "Route error [{}] {} {} — {}",
        request_id,
        request.method,
        request.url.path,
        safe_message,
    )
    return request_id


def build_internal_error_payload(request: Request, exc: Exception) -> dict[str, Any]:
    request_id = log_route_exception(request, exc)
    payload: dict[str, Any] = {
        "error": "internal_server_error",
        "message": (
            "Something went wrong. Please contact support with this request ID."
            if settings.is_production
            else "Unexpected server error."
        ),
        "request_id": request_id,
    }
    if settings.is_development:
        payload["debug"] = {
            "exception_type": type(exc).__name__,
            "location": _safe_exception_location(exc),
            "detail": redact_text(str(exc))[:500],
        }
    return payload


def build_http_error_payload(request: Request, exc: StarletteHTTPException) -> dict[str, Any]:
    detail = exc.detail
    if isinstance(detail, dict):
        payload = dict(detail)
    elif isinstance(detail, list):
        payload = {"error": "validation_error", "detail": detail}
    else:
        payload = {"error": "http_error", "message": str(detail)}
    if exc.status_code >= 500:
        payload.setdefault("request_id", get_request_id(request))
    return payload


def _html_error_page(request_id: str, message: str, *, is_admin: bool = False) -> str:
    logs_link = ""
    if is_admin:
        logs_link = (
            '<a class="btn btn-sm btn-outline-secondary" href="/system/logs">Open System Logs</a> '
        )
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="light" data-bs-theme="light"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DitakNet — Error</title>
<link href="/static/vendor/bootstrap/bootstrap.min.css" rel="stylesheet">
<link href="/static/css/theme.css" rel="stylesheet">
<link href="/static/css/app.css" rel="stylesheet">
<script src="/static/js/theme.js"></script>
</head><body>
<div class="container py-5" style="max-width:640px">
  <div class="card shadow-sm border-0">
    <div class="card-body p-4">
      <h1 class="h4 text-danger mb-2">Something went wrong</h1>
      <p class="text-muted mb-3">{message}</p>
      <div class="alert alert-light border small font-monospace mb-3">Request ID: <span id="error-request-id">{request_id}</span></div>
      <div class="d-flex flex-wrap gap-2">
        <button type="button" class="btn btn-sm btn-primary" onclick="location.reload()">Retry</button>
        <button type="button" class="btn btn-sm btn-outline-secondary" onclick="navigator.clipboard.writeText(document.getElementById('error-request-id').textContent)">Copy request ID</button>
        {logs_link}
        <a class="btn btn-sm btn-outline-secondary" href="/dashboard">Dashboard</a>
      </div>
    </div>
  </div>
</div>
</body></html>"""


def install_request_id_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def assign_request_id(request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


def install_asyncio_exception_handler() -> None:
    """Log exceptions that escape asyncio tasks on the running loop."""

    def _handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
        exc = context.get("exception")
        message = context.get("message", "asyncio error")
        if exc is not None:
            logger.error("Asyncio loop error ({}): {}", message, redact_text(str(exc)))
        else:
            logger.error("Asyncio loop error: {}", message)

    try:
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(_handler)
    except RuntimeError:
        pass


def create_background_task(coro, *, name: str | None = None) -> asyncio.Task:
    """Schedule a coroutine and always log unhandled failures."""

    def _log_task_result(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        task_name = task.get_name() or "background-task"
        logger.error("Background task '{}' failed: {}", task_name, redact_text(str(exc)))

    task = asyncio.create_task(coro, name=name)
    task.add_done_callback(_log_task_result)
    return task


def install_fastapi_exception_handlers(app: FastAPI) -> None:
    """Return stable JSON/HTML for unhandled route and HTTP exceptions."""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        if 300 <= exc.status_code < 400:
            headers = dict(exc.headers or {})
            location = headers.get("Location") or headers.get("location")
            if location:
                return RedirectResponse(url=location, status_code=exc.status_code, headers=headers)

        if exc.status_code >= 500:
            logger.bind(
                request_id=get_request_id(request),
                path=request.url.path,
                method=request.method,
                user=_user_label(request) or "anonymous",
                status_code=exc.status_code,
            ).error("HTTP {} on {} {}", exc.status_code, request.method, request.url.path)

        payload = build_http_error_payload(request, exc)
        if _wants_html(request) and exc.status_code >= 400:
            is_admin = (_user_label(request) or "") == getattr(settings, "admin_username", "admin")
            title_map = {
                400: "Bad Request",
                401: "Unauthorized",
                403: "Access Denied",
                404: "Page Not Found",
                405: "Method Not Allowed",
                409: "Conflict",
                422: "Validation Error",
                429: "Too Many Requests",
            }
            title = title_map.get(exc.status_code, "Error") if exc.status_code < 500 else "Something went wrong"
            return HTMLResponse(
                _html_error_page(
                    payload.get("request_id", get_request_id(request)),
                    str(payload.get("message") or title),
                    is_admin=is_admin,
                ),
                status_code=exc.status_code,
            )
        return JSONResponse(status_code=exc.status_code, content=payload)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        payload = build_internal_error_payload(request, exc)
        if _wants_html(request):
            is_admin = (_user_label(request) or "") == getattr(settings, "admin_username", "admin")
            return HTMLResponse(
                _html_error_page(payload["request_id"], payload["message"], is_admin=is_admin),
                status_code=500,
                headers={REQUEST_ID_HEADER: payload["request_id"]},
            )
        return JSONResponse(
            status_code=500,
            content=payload,
            headers={REQUEST_ID_HEADER: payload["request_id"]},
        )
