"""Regression tests for web output escaping and bounded SQL helpers."""

from __future__ import annotations

import asyncio

import pytest

from ditaknet.web import routes


def test_status_badge_escapes_html_in_unexpected_state() -> None:
    payload = '<img src=x onerror="alert(1)">'

    rendered = str(routes.render_status_badge(payload))

    assert rendered.startswith('<span class="badge bg-secondary">')
    assert "<img" not in rendered.lower()
    assert "&lt;IMG" in rendered
    assert "&gt;" in rendered


def test_status_badge_preserves_expected_markup_for_known_state() -> None:
    assert str(routes.render_status_badge("ok")) == (
        '<span class="badge bg-success">OK</span>'
    )


def test_safe_table_count_rejects_identifier_injection_before_database_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_database_access():
        pytest.fail("Rejected table identifiers must not reach the database")

    monkeypatch.setattr(routes.db, "get_db", unexpected_database_access)

    result = asyncio.run(routes._safe_table_count("hosts; DROP TABLE hosts; --"))

    assert result == 0


def test_safe_table_count_uses_only_the_allowlisted_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StubConnection:
        def __init__(self) -> None:
            self.queries: list[str] = []

        async def execute_fetchall(self, query: str):
            self.queries.append(query)
            return [{"cnt": 7}]

    connection = StubConnection()

    async def get_stub_database() -> StubConnection:
        return connection

    monkeypatch.setattr(routes.db, "get_db", get_stub_database)

    result = asyncio.run(routes._safe_table_count("hosts"))

    assert result == 7
    assert connection.queries == ["SELECT COUNT(*) AS cnt FROM hosts"]
