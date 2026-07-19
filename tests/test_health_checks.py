"""Unit tests for liveness and deep-readiness aggregation."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from ditaknet import health


class _Connection:
    async def execute_fetchall(self, _query: str) -> list[tuple[int]]:
        return [(1,)]


class _Scheduler:
    async def status(self) -> dict[str, object]:
        return {"running": True, "job_count": 3}


def test_basic_health_is_cheap_and_exposes_build_identity() -> None:
    payload = asyncio.run(health.basic_health())

    assert payload["status"] == "healthy"
    assert payload["app_name"]
    assert payload["version"]
    assert payload["timestamp"].endswith("+00:00")


def test_deep_health_reports_all_critical_components(
    monkeypatch,
) -> None:
    directory = {
        "ok": True,
        "writable": True,
        "path": "/tmp/ditaknet-test",
        "error": None,
    }
    monkeypatch.setattr(health, "directory_status", lambda _path: dict(directory))
    monkeypatch.setattr(health.db, "get_db", AsyncMock(return_value=_Connection()))
    monkeypatch.setattr(
        health.db,
        "schema_health",
        AsyncMock(return_value={"ok": True, "status": "pass", "missing": []}),
    )
    monkeypatch.setattr(
        health,
        "_license_health",
        AsyncMock(return_value={"ok": True, "status": "pass", "valid": True}),
    )
    monkeypatch.setattr(
        health,
        "_settings_health",
        AsyncMock(return_value={"ok": True, "status": "pass", "setup_complete": True}),
    )
    monkeypatch.setattr(
        health,
        "_static_assets_health",
        lambda: {"ok": True, "status": "pass", "missing_assets": []},
    )
    monkeypatch.setattr(health, "get_scheduler", lambda: _Scheduler())

    payload = asyncio.run(health.deep_health())

    assert payload["status"] == "healthy"
    assert payload["overall_status"] == "pass"
    assert payload["database"]["status"] == "pass"
    assert payload["migrations"]["ok"] is True
    assert payload["scheduler"]["running"] is True
    assert payload["failed_checks"] == []


def test_deep_health_is_unhealthy_when_database_is_unavailable(
    monkeypatch,
) -> None:
    directory = {"ok": True, "writable": True, "path": "/tmp/test", "error": None}
    monkeypatch.setattr(health, "directory_status", lambda _path: dict(directory))
    monkeypatch.setattr(
        health.db,
        "get_db",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )
    monkeypatch.setattr(
        health.db,
        "schema_health",
        AsyncMock(return_value={"ok": False, "status": "fail", "missing": ["hosts"]}),
    )
    monkeypatch.setattr(
        health,
        "_license_health",
        AsyncMock(return_value={"ok": True, "status": "pass"}),
    )
    monkeypatch.setattr(
        health,
        "_settings_health",
        AsyncMock(return_value={"ok": True, "status": "pass"}),
    )
    monkeypatch.setattr(
        health,
        "_static_assets_health",
        lambda: {"ok": True, "status": "pass", "missing_assets": []},
    )
    monkeypatch.setattr(health, "get_scheduler", lambda: _Scheduler())

    payload = asyncio.run(health.deep_health())

    assert payload["status"] == "unhealthy"
    assert payload["overall_status"] == "fail"
    assert payload["database"]["ok"] is False
    assert payload["database"]["error"] == "RuntimeError"
