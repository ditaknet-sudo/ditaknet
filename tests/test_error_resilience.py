"""Regression tests for Request ID logging, safe errors, and storage checks."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from loguru import logger

from ditaknet.resilience import (
    REQUEST_ID_HEADER,
    install_fastapi_exception_handlers,
    install_request_id_middleware,
    log_route_exception,
)
from ditaknet.utils.paths import directory_status


@pytest.fixture()
def error_app() -> FastAPI:
    app = FastAPI()
    install_request_id_middleware(app)
    install_fastapi_exception_handlers(app)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("synthetic dashboard failure")

    return app


def test_unhandled_exception_returns_safe_json_with_request_id(error_app: FastAPI) -> None:
    client = TestClient(error_app, raise_server_exceptions=False)
    response = client.get("/boom", headers={"Accept": "application/json"})
    assert response.status_code == 500
    payload = response.json()
    assert payload["error"] == "internal_server_error"
    assert payload["message"] == "Unexpected server error"
    assert payload["request_id"].startswith("req_")
    assert "synthetic dashboard failure" not in payload["message"]
    assert "Traceback" not in str(payload)
    assert response.headers.get(REQUEST_ID_HEADER) == payload["request_id"]


def test_request_id_appears_in_log_message(error_app: FastAPI) -> None:
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="ERROR")
    try:
        client = TestClient(error_app, raise_server_exceptions=False)
        fixed = "req_c3a33a2a36a8"
        response = client.get(
            "/boom",
            headers={"Accept": "application/json", REQUEST_ID_HEADER: fixed},
        )
        assert response.status_code == 500
        assert response.json()["request_id"] == fixed
        assert any(fixed in msg and "Route error" in msg for msg in messages), messages
        assert any("RuntimeError" in msg for msg in messages)
    finally:
        logger.remove(sink_id)


def test_html_error_page_is_safe(error_app: FastAPI) -> None:
    client = TestClient(error_app, raise_server_exceptions=False)
    response = client.get("/boom", headers={"Accept": "text/html"})
    assert response.status_code == 500
    body = response.text
    assert "Something went wrong" in body
    assert "Unexpected server error" in body
    assert "Request ID:" in body
    assert "Traceback" not in body
    assert "synthetic dashboard failure" not in body
    assert 'id="error-retry-btn"' in body


def test_directory_status_ok_for_writable(tmp_path: Path) -> None:
    target = tmp_path / "data"
    status = directory_status(target)
    assert status["ok"] is True
    assert status["writable"] is True
    assert Path(status["path"]).exists()


def test_directory_status_fails_clearly_for_file_path(tmp_path: Path) -> None:
    file_path = tmp_path / "not-a-dir.txt"
    file_path.write_text("x", encoding="utf-8")
    status = directory_status(file_path)
    assert status["ok"] is False
    assert status["error"]


def test_safe_numeric_and_license_helpers() -> None:
    from ditaknet.assistant.recommendations import _license_near_limit
    from ditaknet.core.dashboard_overview import _safe_ms
    from ditaknet.core.device_monitoring import _safe_float

    assert _safe_float("12.5") == 12.5
    assert _safe_float("timeout") is None
    assert _safe_float("") is None
    assert _safe_ms("bad") is None
    assert _license_near_limit({"max_hosts": None, "used_hosts": 99}) is False
    assert _license_near_limit({"max_hosts": "unlimited", "used_hosts": 99}) is False
    assert _license_near_limit({"max_hosts": 10, "used_hosts": 9}) is True
