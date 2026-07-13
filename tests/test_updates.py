"""Tests for DitakNet update checker (notify-only, no auto-apply)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from ditaknet.core import updates as updates_mod
from ditaknet.core.updates import (
    compare_versions,
    dismiss_update_version,
    get_update_status,
    is_newer_version,
    parse_semver,
    set_check_enabled,
    snooze_update_banner,
    validate_manifest,
    verify_file_sha256,
)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def app_db(tmp_path, monkeypatch):
    from ditaknet import database as db
    from ditaknet.config import settings

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", str(data))
    monkeypatch.setattr(settings, "database_path", str(data / "test.db"))
    monkeypatch.setattr(settings, "app_version", "2.0.1")
    monkeypatch.setattr(settings, "app_update_check_enabled", True)
    monkeypatch.setattr(settings, "app_latest_version", "")
    monkeypatch.setattr(settings, "app_latest_image_tag", "")
    monkeypatch.setattr(
        settings,
        "app_update_manifest_url",
        "https://example.test/update-manifest.json",
    )
    monkeypatch.setattr(settings, "app_update_check_url", "")
    monkeypatch.setattr(settings, "app_update_manifest_signing_key", "")
    monkeypatch.setattr(settings, "github_repository", "ditaknet-sudo/ditaknet")
    updates_mod._CACHE["payload"] = None
    updates_mod._CACHE["expires_at"] = 0.0

    async def _setup():
        await db.init_db(str(data / "test.db"))
        await db.set_app_setting(updates_mod._KEY_ENABLED_OVERRIDE, "")

    _run(_setup())
    yield

    async def _teardown():
        await db.close_db()

    _run(_teardown())


def _client_with(get_fn):
    client = AsyncMock()
    client.get = get_fn
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _mock_response(status: int, data: dict | None = None, etag: str | None = None, text: str = ""):
    response = MagicMock()
    response.status_code = status
    response.headers = {"ETag": etag} if etag else {}
    response.content = json.dumps(data).encode() if data is not None else b""
    response.text = text or (json.dumps(data) if data is not None else "")
    response.json = MagicMock(return_value=data)

    def _raise():
        if status >= 400 and status != 304:
            raise httpx.HTTPStatusError("err", request=MagicMock(), response=response)

    response.raise_for_status = _raise if status >= 400 and status != 304 else MagicMock()
    return response


def test_semver_ordering():
    assert is_newer_version("2.0.10", "2.0.9")
    assert not is_newer_version("2.0.9", "2.0.10")
    assert not is_newer_version("2.0.1", "2.0.1")
    assert is_newer_version("2.1.0", "2.0.99")
    assert compare_versions("2.0.10", "2.0.9") == 1
    assert compare_versions("2.0.9", "2.0.10") == -1
    assert compare_versions("2.0.1", "2.0.1") == 0
    assert parse_semver("") is None
    assert parse_semver("not-a-version") is None
    assert not is_newer_version("", "2.0.1")
    assert not is_newer_version("latest", "2.0.1")


def test_validate_manifest_ok():
    manifest = validate_manifest(
        {
            "channel": "stable",
            "latest_version": "2.0.2",
            "minimum_supported_version": "2.0.0",
            "release_url": "https://github.com/ditaknet-sudo/ditaknet/releases/tag/v2.0.2",
            "docker_image": "ghcr.io/ditaknet-sudo/ditaknet:2.0.2",
            "critical": False,
            "message": {"en": "DitakNet 2.0.2 is available", "hy": "Հասանելի է"},
        }
    )
    assert manifest["latest_version"] == "2.0.2"
    assert manifest["critical"] is False


def test_validate_manifest_invalid():
    with pytest.raises(ValueError):
        validate_manifest({"latest_version": ""})
    with pytest.raises(ValueError):
        validate_manifest({"latest_version": "abc"})
    with pytest.raises(ValueError):
        validate_manifest("nope")
    with pytest.raises(ValueError):
        validate_manifest({"latest_version": "2.0.2", "checksums": "bad"})


def test_sha256_helper():
    content = b"hello-ditaknet"
    real = hashlib.sha256(content).hexdigest()
    assert verify_file_sha256(content, real) is True
    assert verify_file_sha256(content, f"sha256:{real}") is True
    assert verify_file_sha256(content, "0" * 64) is False


def test_update_available(app_db, monkeypatch):
    monkeypatch.setattr(updates_mod.settings, "app_version", "2.0.1")

    async def fake_get(url, headers=None):
        assert url.startswith("https://")
        assert "User-Agent" in (headers or {})
        return _mock_response(
            200,
            {
                "latest_version": "2.0.2",
                "docker_image": "ghcr.io/ditaknet-sudo/ditaknet:2.0.2",
                "release_url": "https://example.test/r",
                "critical": False,
                "message": {"en": "DitakNet 2.0.2 is available"},
            },
            etag='"v1"',
        )

    with patch("httpx.AsyncClient", return_value=_client_with(fake_get)):
        payload = _run(get_update_status(force=True))

    assert payload["update_available"] is True
    assert payload["latest_version"] == "2.0.2"
    assert payload["current_version"] == "2.0.1"
    assert payload["critical"] is False
    assert not payload.get("error")
    assert payload["auto_update_enabled"] is False


def test_same_version(app_db, monkeypatch):
    monkeypatch.setattr(updates_mod.settings, "app_version", "2.0.2")

    async def fake_get(url, headers=None):
        return _mock_response(200, {"latest_version": "2.0.2", "docker_image": "x:2.0.2"})

    with patch("httpx.AsyncClient", return_value=_client_with(fake_get)):
        payload = _run(get_update_status(force=True))
    assert payload["update_available"] is False
    assert payload["show_banner"] is False


def test_installed_newer_than_manifest(app_db, monkeypatch):
    monkeypatch.setattr(updates_mod.settings, "app_version", "2.1.0")

    async def fake_get(url, headers=None):
        return _mock_response(200, {"latest_version": "2.0.2"})

    with patch("httpx.AsyncClient", return_value=_client_with(fake_get)):
        payload = _run(get_update_status(force=True))
    assert payload["update_available"] is False


def test_bad_manifest(app_db):
    async def always_bad(url, headers=None):
        return _mock_response(200, {"tag_name": "nightly", "latest_version": "nightly"})

    with patch("httpx.AsyncClient", return_value=_client_with(always_bad)):
        payload = _run(get_update_status(force=True))
    assert payload["update_available"] is False
    assert payload.get("error") or payload.get("source") in {"error", "cached_after_error"}


def test_timeout(app_db):
    async def boom(url, headers=None):
        raise httpx.TimeoutException("timeout")

    with patch("httpx.AsyncClient", return_value=_client_with(boom)):
        payload = _run(get_update_status(force=True))
    assert payload["update_available"] is False
    assert payload.get("error")


def test_http_500(app_db):
    async def fake_get(url, headers=None):
        return _mock_response(500, text="server error")

    with patch("httpx.AsyncClient", return_value=_client_with(fake_get)):
        payload = _run(get_update_status(force=True))
    assert payload.get("error")


def test_offline(app_db):
    async def boom(url, headers=None):
        raise httpx.ConnectError("no internet")

    with patch("httpx.AsyncClient", return_value=_client_with(boom)):
        payload = _run(get_update_status(force=True))
    assert payload["update_available"] is False
    assert "ConnectError" in str(payload.get("error") or payload.get("message") or "")


def test_github_rate_limit_then_fallback(app_db, monkeypatch):
    monkeypatch.setattr(updates_mod.settings, "app_version", "2.0.1")

    async def fake_get(url, headers=None):
        if "update-manifest" in url:
            return _mock_response(403, text="API rate limit exceeded")
        return _mock_response(
            200,
            {
                "tag_name": "v2.0.3",
                "html_url": "https://github.com/ditaknet-sudo/ditaknet/releases/tag/v2.0.3",
                "body": "notes",
            },
        )

    with patch("httpx.AsyncClient", return_value=_client_with(fake_get)):
        payload = _run(get_update_status(force=True))
    assert payload["latest_version"] == "2.0.3"
    assert payload["update_available"] is True
    assert payload["source"] == "github_releases"


def test_etag_304(app_db, monkeypatch):
    monkeypatch.setattr(updates_mod.settings, "app_version", "2.0.1")
    from ditaknet import database as db

    stored = {
        "status": "up_to_date",
        "source": "manifest",
        "latest_version": "2.0.1",
        "update_available": False,
        "critical": False,
        "current_version": "2.0.1",
        "channel": "stable",
    }

    async def seed():
        await db.set_app_setting(updates_mod._KEY_PAYLOAD, json.dumps(stored))
        await db.set_app_setting(updates_mod._KEY_ETAG, '"abc"')

    _run(seed())

    async def fake_get(url, headers=None):
        assert headers.get("If-None-Match") == '"abc"'
        return _mock_response(304, etag='"abc"')

    with patch("httpx.AsyncClient", return_value=_client_with(fake_get)):
        payload = _run(get_update_status(force=False))
    assert payload["latest_version"] == "2.0.1"
    assert payload["update_available"] is False


def test_critical_cannot_fully_dismiss(app_db, monkeypatch):
    monkeypatch.setattr(updates_mod.settings, "app_version", "2.0.1")

    async def fake_get(url, headers=None):
        return _mock_response(
            200,
            {
                "latest_version": "2.0.5",
                "critical": True,
                "docker_image": "ghcr.io/ditaknet-sudo/ditaknet:2.0.5",
                "message": {"en": "Critical"},
            },
        )

    with patch("httpx.AsyncClient", return_value=_client_with(fake_get)):
        payload = _run(get_update_status(force=True))
        assert payload["critical"] is True
        assert payload["can_dismiss"] is False
        dismissed = _run(dismiss_update_version("2.0.5"))
    assert dismissed["critical"] is True
    assert dismissed["can_dismiss"] is False


def test_disabled_update_check(app_db):
    _run(set_check_enabled(False))
    payload = _run(get_update_status(force=True))
    assert payload["source"] == "disabled"
    assert payload["update_available"] is False
    _run(set_check_enabled(True))


def test_persisted_state_survives_reload(app_db, monkeypatch):
    monkeypatch.setattr(updates_mod.settings, "app_version", "2.0.1")

    async def fake_get(url, headers=None):
        return _mock_response(
            200,
            {
                "latest_version": "2.0.4",
                "docker_image": "ghcr.io/ditaknet-sudo/ditaknet:2.0.4",
                "critical": False,
            },
            etag='"persist"',
        )

    with patch("httpx.AsyncClient", return_value=_client_with(fake_get)):
        first = _run(get_update_status(force=True))
    assert first["latest_version"] == "2.0.4"

    updates_mod._CACHE["payload"] = None
    updates_mod._CACHE["expires_at"] = 0.0

    async def offline(url, headers=None):
        raise httpx.ConnectError("down")

    with patch("httpx.AsyncClient", return_value=_client_with(offline)):
        second = _run(get_update_status(force=True))
    assert second.get("latest_version") == "2.0.4"
    assert second.get("update_available") is True


def test_health_unaffected_by_update_failure(app_db):
    from ditaknet.health import basic_health

    async def boom(url, headers=None):
        raise httpx.ConnectError("down")

    with patch("httpx.AsyncClient", return_value=_client_with(boom)):
        _run(get_update_status(force=True))
        health = _run(basic_health())
    assert health.get("status") == "healthy"


def test_snooze_hides_banner(app_db, monkeypatch):
    monkeypatch.setattr(updates_mod.settings, "app_version", "2.0.0")

    async def fake_get(url, headers=None):
        return _mock_response(
            200,
            {"latest_version": "2.0.1", "critical": False, "message": {"en": "up"}},
        )

    with patch("httpx.AsyncClient", return_value=_client_with(fake_get)):
        _run(get_update_status(force=True))
        snoozed = _run(snooze_update_banner(hours=24))
    assert snoozed["show_banner"] is False
    assert snoozed["snoozed"] is True
