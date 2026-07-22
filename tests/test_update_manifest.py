"""Update manifest parsing, signing, versioning, and failure regression tests."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from unittest.mock import AsyncMock

import pytest
import httpx

from ditaknet import database as db
from ditaknet.core import updates
from ditaknet.core.update_metadata import public_key_base64, sign_manifest


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


def _empty_state(payload: dict | None = None, *, etag: str | None = None) -> dict:
    return {
        "last_checked_at": None,
        "last_success_at": None,
        "etag": etag,
        "payload": payload,
        "failures": 0,
        "backoff_until": None,
        "dismissed_version": None,
        "snooze_until": None,
    }


def _signed_v2_manifest(
    *,
    channel: str = "stable",
    version: str = "2.1.0",
    sequence: int = 21,
) -> tuple[dict, bytes, dict[str, dict[str, str]]]:
    private_key = base64.b64encode(bytes(range(32))).decode("ascii")
    manifest = {
        "schema_version": 2,
        "channel": channel,
        "version": version,
        "docker_image": f"ghcr.io/ditaknet-sudo/ditaknet:{version}",
        "image_digest": "sha256:" + "a" * 64,
        "platform_digests": {
            "linux/amd64": "sha256:" + "b" * 64,
            "linux/arm64": "sha256:" + "c" * 64,
        },
        "release_url": (
            f"https://github.com/ditaknet-sudo/ditaknet/releases/tag/v{version}"
        ),
        "source_commit": "d" * 40,
        "published_at": "2026-07-20T12:34:56Z",
        "sequence": sequence,
        "compatibility": {
            "minimum_current_version": "2.0.0",
            "maximum_current_version": "2.0.99",
            "requires_backup": True,
            "allow_major_upgrade": False,
            "target_schema_revision": 1,
            "backup_format_version": 2,
            "rollback_policy": "state_restore_required",
        },
        "critical": False,
        "message": {"en": f"DitakNet {version} is available"},
    }
    signed = sign_manifest(
        manifest,
        key_id=f"{channel}-test",
        private_key=private_key,
    )
    raw = json.dumps(signed, ensure_ascii=False).encode("utf-8")
    keyring = {
        "stable": {},
        "beta": {},
    }
    keyring[channel][f"{channel}-test"] = public_key_base64(private_key)
    return signed, raw, keyring


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
    assert (
        updates.verify_manifest_signature(
            raw,
            base64.b64encode(bytes.fromhex(signature)).decode("ascii"),
        )
        is True
    )

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
    monkeypatch.setattr(updates.settings, "app_update_signature_required", False)
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
    monkeypatch.setattr(
        updates.settings, "app_update_manifest_signing_key", "required-key"
    )
    monkeypatch.setattr(updates.settings, "app_latest_version", "")
    monkeypatch.setattr(updates.settings, "app_latest_image_tag", "")
    updates._CACHE.update({"expires_at": 0.0, "payload": None})

    result = asyncio.run(updates.check_for_updates(force=True))

    assert fetch.await_count == 1
    assert result["status"] == "error"
    assert result["update_available"] is False
    assert "signature" in result["error_message"].lower()


def test_official_channel_urls_are_isolated_and_custom_override_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(updates.settings, "app_update_manifest_url", "")
    monkeypatch.setattr(updates.settings, "app_update_check_url", "")
    monkeypatch.setattr(
        updates.settings,
        "app_update_stable_manifest_url",
        "https://raw.githubusercontent.com/ditaknet-sudo/ditaknet/update-feed/stable.json",
    )
    monkeypatch.setattr(
        updates.settings,
        "app_update_beta_manifest_url",
        "https://raw.githubusercontent.com/ditaknet-sudo/ditaknet/update-feed/beta.json",
    )

    assert updates._manifest_url("stable").endswith("/stable.json")
    assert updates._manifest_url("beta").endswith("/beta.json")

    monkeypatch.setattr(
        updates.settings,
        "app_update_manifest_url",
        "https://github.com/ditaknet-sudo/ditaknet/custom.json",
    )
    assert updates._manifest_url("stable").endswith("/custom.json")
    assert updates._manifest_url("beta").endswith("/custom.json")


def test_manifest_channel_mismatch_is_rejected_not_merely_logged() -> None:
    signed, _, _ = _signed_v2_manifest(channel="beta", version="2.1.0-beta.1")

    with pytest.raises(ValueError, match="does not match"):
        updates.validate_manifest(signed, expected_channel="stable")


def test_signed_v2_manifest_becomes_digest_bound_actionable_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed, raw, keyring = _signed_v2_manifest()
    fetch = AsyncMock(return_value=(200, signed, '"v2"', raw))
    monkeypatch.setattr(updates, "_is_check_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(updates, "_load_state", AsyncMock(return_value=_empty_state()))
    monkeypatch.setattr(updates, "_save_success", AsyncMock())
    monkeypatch.setattr(updates, "_save_failure", AsyncMock())
    monkeypatch.setattr(updates, "_fetch_json", fetch)
    monkeypatch.setattr(updates, "_load_public_keyring", lambda: keyring)
    monkeypatch.setattr(updates.settings, "app_update_signature_required", True)
    monkeypatch.setattr(updates.settings, "app_update_manifest_signing_key", "")
    monkeypatch.setattr(updates.settings, "app_update_channel", "stable")
    monkeypatch.setattr(updates.settings, "app_update_manifest_url", "")
    monkeypatch.setattr(updates.settings, "app_update_check_url", "")
    monkeypatch.setattr(updates.settings, "app_latest_version", "")
    monkeypatch.setattr(updates.settings, "app_latest_image_tag", "")
    updates._CACHE.update({"expires_at": 0.0, "payload": None})

    result = asyncio.run(updates.check_for_updates(force=True))

    assert fetch.await_count == 1
    assert result["source"] == "manifest"
    assert result["manifest_trusted"] is True
    assert result["signing_key_id"] == "stable-test"
    assert result["schema_version"] == 2
    assert result["image_digest"] == "sha256:" + "a" * 64
    assert result["update_handoff_available"] is True
    assert result["compatibility"]["requires_backup"] is True


def test_required_policy_rejects_304_from_previously_unsigned_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(updates, "_is_check_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(updates.settings, "app_update_signature_required", True)
    monkeypatch.setattr(updates.settings, "app_update_manifest_signing_key", "")
    monkeypatch.setattr(updates.settings, "app_update_channel", "stable")
    monkeypatch.setattr(updates.settings, "app_update_manifest_url", "")
    monkeypatch.setattr(updates.settings, "app_update_check_url", "")
    monkeypatch.setattr(updates.settings, "app_latest_version", "")
    monkeypatch.setattr(updates.settings, "app_latest_image_tag", "")
    monkeypatch.setattr(
        updates, "_load_public_keyring", lambda: {"stable": {}, "beta": {}}
    )
    policy = updates._trust_policy_id(
        channel="stable", manifest_url=updates._manifest_url("stable")
    )
    cached = {
        "trust_policy_id": policy,
        "manifest_trusted": False,
        "latest_version": "9.9.9",
        "update_available": True,
        "channel": "stable",
    }
    monkeypatch.setattr(
        updates,
        "_load_state",
        AsyncMock(return_value=_empty_state(cached, etag='"unsigned"')),
    )
    fetch = AsyncMock(return_value=(304, None, '"unsigned"', b""))
    monkeypatch.setattr(updates, "_fetch_json", fetch)
    monkeypatch.setattr(updates, "_save_failure", AsyncMock())
    updates._CACHE.update({"expires_at": 0.0, "payload": None})

    result = asyncio.run(updates.check_for_updates(force=False))

    assert fetch.await_count == 1
    assert result["status"] == "error"
    assert result["update_available"] is False
    assert "304" in result["error_message"]


def test_persisted_failure_backoff_never_reenables_cached_handoff_after_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_url = "https://example.invalid/stable.json"
    monkeypatch.setattr(updates, "_is_check_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(updates.settings, "app_update_signature_required", True)
    monkeypatch.setattr(updates.settings, "app_update_manifest_signing_key", "")
    monkeypatch.setattr(updates.settings, "app_update_channel", "stable")
    monkeypatch.setattr(updates.settings, "app_update_manifest_url", manifest_url)
    monkeypatch.setattr(updates.settings, "app_update_check_url", "")
    monkeypatch.setattr(updates.settings, "app_latest_version", "")
    monkeypatch.setattr(updates.settings, "app_latest_image_tag", "")
    policy = updates._trust_policy_id(
        channel="stable",
        manifest_url=manifest_url,
    )
    cached = {
        "trust_policy_id": policy,
        "manifest_trusted": True,
        "schema_version": 2,
        "source": "manifest",
        "status": "update_available",
        "latest_version": "2.0.2",
        "update_available": True,
        "channel": "stable",
        "docker_image": "ghcr.io/ditaknet-sudo/ditaknet:2.0.2",
        "image_digest": "sha256:" + "a" * 64,
    }
    state = _empty_state(cached)
    state.update(
        {
            "failures": 1,
            "backoff_until": "2999-01-01T00:00:00+00:00",
            "last_error": "RuntimeError: feed unavailable",
        }
    )
    monkeypatch.setattr(updates, "_load_state", AsyncMock(return_value=state))
    fetch = AsyncMock()
    monkeypatch.setattr(updates, "_fetch_json", fetch)
    updates._CACHE.update({"expires_at": 0.0, "payload": None})

    result = asyncio.run(updates.check_for_updates(force=False))

    fetch.assert_not_awaited()
    assert result["status"] == "error"
    assert result["source"] == "cached_after_error"
    assert result["error_message"] == "RuntimeError: feed unavailable"
    assert result["update_available"] is True
    assert result["manifest_trusted"] is True
    assert result["update_handoff_available"] is False


def test_keyring_policy_change_drops_old_etag_and_revalidates_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed, raw, keyring = _signed_v2_manifest()
    cached = {
        "trust_policy_id": "old-policy",
        "manifest_trusted": True,
        "latest_version": "2.0.9",
        "channel": "stable",
        "sequence": 20,
    }
    seen_etags: list[str | None] = []

    async def fetch(_: str, *, etag: str | None = None, **__: object):
        seen_etags.append(etag)
        return 200, signed, '"new"', raw

    monkeypatch.setattr(updates, "_is_check_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(
        updates,
        "_load_state",
        AsyncMock(return_value=_empty_state(cached, etag='"old"')),
    )
    monkeypatch.setattr(updates, "_fetch_json", fetch)
    monkeypatch.setattr(updates, "_save_success", AsyncMock())
    monkeypatch.setattr(updates, "_save_failure", AsyncMock())
    monkeypatch.setattr(updates, "_load_public_keyring", lambda: keyring)
    monkeypatch.setattr(updates.settings, "app_update_signature_required", True)
    monkeypatch.setattr(updates.settings, "app_update_manifest_signing_key", "")
    monkeypatch.setattr(updates.settings, "app_update_channel", "stable")
    monkeypatch.setattr(updates.settings, "app_update_manifest_url", "")
    monkeypatch.setattr(updates.settings, "app_update_check_url", "")
    monkeypatch.setattr(updates.settings, "app_latest_version", "")
    monkeypatch.setattr(updates.settings, "app_latest_image_tag", "")
    updates._CACHE.update({"expires_at": 0.0, "payload": None})

    result = asyncio.run(updates.check_for_updates(force=False))

    assert seen_etags == [None]
    assert result["manifest_trusted"] is True


def test_signed_manifest_sequence_replay_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed, raw, keyring = _signed_v2_manifest(sequence=21)
    monkeypatch.setattr(updates.settings, "app_update_signature_required", True)
    monkeypatch.setattr(updates.settings, "app_update_manifest_signing_key", "")
    monkeypatch.setattr(updates.settings, "app_update_channel", "stable")
    monkeypatch.setattr(updates.settings, "app_update_manifest_url", "")
    monkeypatch.setattr(updates.settings, "app_update_check_url", "")
    monkeypatch.setattr(updates.settings, "app_latest_version", "")
    monkeypatch.setattr(updates.settings, "app_latest_image_tag", "")
    monkeypatch.setattr(updates, "_load_public_keyring", lambda: keyring)
    policy = updates._trust_policy_id(
        channel="stable", manifest_url=updates._manifest_url("stable")
    )
    cached = {
        "trust_policy_id": policy,
        "manifest_trusted": True,
        "manifest_hash": "f" * 64,
        "schema_version": 2,
        "latest_version": "2.2.0",
        "update_available": True,
        "channel": "stable",
        "sequence": 22,
    }
    monkeypatch.setattr(updates, "_is_check_enabled", AsyncMock(return_value=True))
    state = _empty_state(cached, etag='"newer"')
    state["replay_state"] = {"stable": {"sequence": 22, "manifest_hash": "f" * 64}}
    monkeypatch.setattr(updates, "_load_state", AsyncMock(return_value=state))
    monkeypatch.setattr(
        updates,
        "_fetch_json",
        AsyncMock(return_value=(200, signed, '"older"', raw)),
    )
    monkeypatch.setattr(updates, "_save_failure", AsyncMock())
    monkeypatch.setattr(updates, "_save_success", AsyncMock())
    updates._CACHE.update({"expires_at": 0.0, "payload": None})

    result = asyncio.run(updates.check_for_updates(force=True))

    assert result["status"] == "error"
    assert result["source"] == "cached_after_error"
    assert "replay" in result["error_message"].lower()
    assert result["update_handoff_available"] is False


def test_replay_anchor_survives_key_policy_and_channel_cache_changes() -> None:
    state = {
        "payload": {
            "channel": "beta",
            "schema_version": 2,
            "manifest_trusted": True,
            "sequence": 999,
            "manifest_hash": "b" * 64,
        },
        "replay_state": {
            "stable": {"sequence": 22, "manifest_hash": "a" * 64},
            "beta": {"sequence": 999, "manifest_hash": "b" * 64},
        },
    }

    with pytest.raises(ValueError, match="replay/downgrade"):
        updates._reject_manifest_replay(
            state,
            channel="stable",
            sequence=21,
            manifest_hash="c" * 64,
        )
    with pytest.raises(ValueError, match="reused"):
        updates._reject_manifest_replay(
            state,
            channel="stable",
            sequence=22,
            manifest_hash="c" * 64,
        )
    updates._reject_manifest_replay(
        state,
        channel="stable",
        sequence=22,
        manifest_hash="a" * 64,
    )


def test_trusted_payload_and_replay_anchor_are_the_first_atomic_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    atomic_write = AsyncMock(side_effect=RuntimeError("synthetic atomic failure"))
    single_write = AsyncMock()
    monkeypatch.setattr(db, "get_app_setting", AsyncMock(return_value=""))
    monkeypatch.setattr(db, "set_app_settings_atomic", atomic_write)
    monkeypatch.setattr(db, "set_app_setting", single_write)
    payload = {
        "channel": "stable",
        "schema_version": 2,
        "manifest_trusted": True,
        "sequence": 23,
        "manifest_hash": "d" * 64,
    }

    with pytest.raises(RuntimeError, match="synthetic atomic failure"):
        asyncio.run(updates._save_success(payload, '"etag"'))

    written = atomic_write.await_args.args[0]
    assert updates._KEY_PAYLOAD in written
    assert updates._KEY_REPLAY_STATE in written
    single_write.assert_not_awaited()


def test_manifest_fetch_rejects_oversized_body_before_json_parse() -> None:
    async def exercise() -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"Content-Length": str(updates._MAX_MANIFEST_BYTES + 1)},
                content=b"{}",
                request=request,
            )
        )
        async with httpx.AsyncClient(
            transport=transport, follow_redirects=True
        ) as client:
            with pytest.raises(ValueError, match="too large"):
                await updates._fetch_json(
                    "https://example.invalid/manifest.json", client=client
                )

    asyncio.run(exercise())


def test_manifest_fetch_rejects_https_to_http_redirect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "https":
            return httpx.Response(
                302,
                headers={"Location": "http://example.invalid/manifest.json"},
                request=request,
            )
        return httpx.Response(200, json={}, request=request)

    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), follow_redirects=True
        ) as client:
            with pytest.raises(ValueError, match="non-HTTPS"):
                await updates._fetch_json(
                    "https://example.invalid/manifest.json", client=client
                )

    asyncio.run(exercise())
