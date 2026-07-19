"""Update manifest parsing, signing, versioning, and failure regression tests."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import pytest

from ditaknet.core import updates


def _signed_manifest(key: str, **overrides: object) -> tuple[dict, bytes, str]:
    manifest: dict[str, object] = {
        "channel": "stable",
        "latest_version": "2.1.0",
        "minimum_supported_version": "2.0.0",
        "docker_image": "ghcr.io/ditaknet-sudo/ditaknet:2.1.0",
        "message": {"en": "DitakNet 2.1.0 is available"},
        **overrides,
    }
    signature = hmac.new(
        key.encode("utf-8"),
        updates.canonical_manifest_payload(manifest),
        hashlib.sha256,
    ).hexdigest()
    signed = {**manifest, "signature": signature}
    # Deliberately use pretty, unsorted JSON: canonical verification must not
    # depend on transport whitespace or key insertion order.
    raw = json.dumps(signed, ensure_ascii=False, indent=2).encode("utf-8")
    return signed, raw, signature


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("v2.4", (2, 4, 0, ())),
        ("Version 2.4.1+build.9", (2, 4, 1, ())),
        ("2.4.1-rc.2", (2, 4, 1, ("rc", "2"))),
        ("", None),
        ("latest", None),
        ("2", None),
    ],
)
def test_parse_semver(value: str, expected: object) -> None:
    assert updates.parse_semver(value) == expected


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("2.0.1", "2.0.0", 1),
        ("2.0.0", "2.0.0", 0),
        ("1.9.9", "2.0.0", -1),
        ("2.0.0", "2.0.0-rc.1", 1),
        ("2.0.0-rc.2", "2.0.0-rc.1", 1),
        ("2.0.0-alpha.1", "2.0.0-alpha.beta", -1),
        ("2.0.0-beta.11", "2.0.0-rc.1", -1),
    ],
)
def test_compare_versions_follows_semver_prerelease_order(
    left: str,
    right: str,
    expected: int,
) -> None:
    assert updates.compare_versions(left, right) == expected


def test_validate_manifest_normalizes_supported_aliases() -> None:
    digest = hashlib.sha256(b"image artifact").hexdigest()
    manifest = updates.validate_manifest(
        {
            "version": "v2.1.0+build.7",
            "minimum_supported_version": "2.0",
            "latest_image_tag": "2.1.0",
            "html_url": "https://github.com/ditaknet-sudo/ditaknet/releases/tag/v2.1.0",
            "critical": True,
            "message": "  Upgrade available  ",
            "upgrade_hint": {"en": "  Back up first  ", "hy": ""},
            "checksums": {"artifact": f"sha256:{digest}"},
        }
    )

    assert manifest["latest_version"] == "2.1.0"
    assert manifest["minimum_supported_version"] == "2.0"
    assert manifest["docker_image"] == "ghcr.io/ditaknet-sudo/ditaknet:2.1.0"
    assert manifest["release_url"].endswith("/v2.1.0")
    assert manifest["message"] == {"en": "Upgrade available"}
    assert manifest["upgrade_hint"] == {"en": "Back up first"}
    assert manifest["critical"] is True


@pytest.mark.parametrize(
    "manifest",
    [
        [],
        {},
        {"latest_version": "latest"},
        {"latest_version": "2.1.0", "minimum_supported_version": "old"},
        {"latest_version": "2.1.0", "checksums": []},
        {"latest_version": "2.1.0", "checksums": {"artifact": "abc123"}},
    ],
)
def test_validate_manifest_rejects_invalid_payloads(manifest: object) -> None:
    with pytest.raises(ValueError):
        updates.validate_manifest(manifest)


def test_embedded_manifest_signature_uses_canonical_unsigned_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "test-signing-key"
    signed, raw, signature = _signed_manifest(key)
    monkeypatch.setattr(updates.settings, "app_update_manifest_signing_key", key)

    assert updates.verify_manifest_signature(raw, signature) is True
    assert updates.verify_manifest_signature(raw, f"sha256={signature}") is True
    assert updates.verify_manifest_signature(
        raw,
        base64.b64encode(bytes.fromhex(signature)).decode("ascii"),
    ) is True

    tampered = json.dumps(
        {**signed, "latest_version": "9.9.9"},
        separators=(",", ":"),
    ).encode("utf-8")
    assert updates.verify_manifest_signature(tampered, signature) is False
    assert updates.verify_manifest_signature(raw, None) is False
    assert updates.verify_manifest_signature(raw, "not-a-signature") is False


def test_file_checksum_verification_rejects_bad_digest() -> None:
    content = b"release artifact"
    digest = hashlib.sha256(content).hexdigest()

    assert updates.verify_file_sha256(content, digest) is True
    assert updates.verify_file_sha256(content, f"sha256:{digest}") is True
    assert updates.verify_file_sha256(content + b"tampered", digest) is False
    assert updates.verify_file_sha256(content, "short") is False


def test_update_check_returns_safe_error_when_all_sources_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "last_checked_at": None,
        "last_success_at": None,
        "etag": None,
        "payload": None,
        "failures": 0,
        "backoff_until": None,
        "dismissed_version": None,
        "snooze_until": None,
    }
    save_failure = AsyncMock()
    fetch_calls: list[str] = []

    async def fail_fetch(url: str, **_: object):
        fetch_calls.append(url)
        raise RuntimeError("synthetic offline failure")

    monkeypatch.setattr(updates, "_is_check_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(updates, "_load_state", AsyncMock(return_value=state))
    monkeypatch.setattr(updates, "_save_failure", save_failure)
    monkeypatch.setattr(updates, "_fetch_json", fail_fetch)
    monkeypatch.setattr(updates.settings, "app_latest_version", "")
    monkeypatch.setattr(updates.settings, "app_latest_image_tag", "")
    updates._CACHE.update({"expires_at": 0.0, "payload": None})

    result = asyncio.run(updates.check_for_updates(force=True))

    assert len(fetch_calls) == 2  # manifest, then GitHub Releases fallback
    save_failure.assert_awaited_once()
    assert result["status"] == "error"
    assert result["source"] == "error"
    assert result["update_available"] is False
    assert "synthetic offline failure" in result["error_message"]


def test_get_update_status_contains_unexpected_internal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        updates,
        "check_for_updates",
        AsyncMock(side_effect=RuntimeError("synthetic internal failure")),
    )

    result = asyncio.run(updates.get_update_status(force=True))

    assert result["status"] == "error"
    assert result["update_available"] is False
    assert "synthetic internal failure" in result["error_message"]


def test_required_manifest_signature_never_downgrades_to_unsigned_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {
        "last_checked_at": None,
        "last_success_at": None,
        "etag": None,
        "payload": None,
        "failures": 0,
        "backoff_until": None,
        "dismissed_version": None,
        "snooze_until": None,
    }
    unsigned = {
        "channel": "stable",
        "latest_version": "2.1.0",
        "docker_image": "ghcr.io/ditaknet-sudo/ditaknet:2.1.0",
    }
    raw = json.dumps(unsigned).encode("utf-8")
    fetch = AsyncMock(return_value=(200, unsigned, None, raw))

    monkeypatch.setattr(updates, "_is_check_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(updates, "_load_state", AsyncMock(return_value=state))
    monkeypatch.setattr(updates, "_save_failure", AsyncMock())
    monkeypatch.setattr(updates, "_fetch_json", fetch)
    monkeypatch.setattr(updates.settings, "app_update_manifest_signing_key", "required-key")
    monkeypatch.setattr(updates.settings, "app_latest_version", "")
    monkeypatch.setattr(updates.settings, "app_latest_image_tag", "")
    updates._CACHE.update({"expires_at": 0.0, "payload": None})

    result = asyncio.run(updates.check_for_updates(force=True))

    assert fetch.await_count == 1
    assert result["status"] == "error"
    assert result["update_available"] is False
    assert "signature" in result["error_message"].lower()
