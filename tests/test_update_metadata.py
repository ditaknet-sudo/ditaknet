"""Strict v2 update metadata and offline release-signing tests."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ditaknet.core.update_metadata import (
    MANIFEST_DOMAIN,
    canonical_manifest_payload,
    public_key_base64,
    sign_manifest,
    validate_update_manifest,
    verified_signature_key_ids,
    verify_manifest_signatures,
)


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_KEY_ONE = base64.b64encode(bytes(range(32))).decode("ascii")
PRIVATE_KEY_TWO = base64.b64encode(bytes(range(32, 64))).decode("ascii")


def _manifest(**overrides: object) -> dict[str, object]:
    manifest: dict[str, object] = {
        "schema_version": 2,
        "channel": "stable",
        "version": "2.1.0",
        "docker_image": "ghcr.io/ditaknet-sudo/ditaknet:2.1.0",
        "image_digest": f"sha256:{'a' * 64}",
        "platform_digests": {
            "linux/amd64": f"sha256:{'b' * 64}",
            "linux/arm64": f"sha256:{'c' * 64}",
        },
        "release_url": (
            "https://github.com/ditaknet-sudo/ditaknet/releases/tag/v2.1.0"
        ),
        "source_commit": "d" * 40,
        "published_at": "2026-07-20T12:34:56Z",
        "sequence": 21,
        "compatibility": {
            "minimum_current_version": "2.0.0",
            "maximum_current_version": "2.0.99",
            "requires_backup": True,
            "allow_major_upgrade": False,
            "target_schema_revision": 2,
            "backup_format_version": 1,
            "rollback_policy": "state_restore_required",
        },
        "critical": False,
        "message": {"en": "DitakNet 2.1.0 is available"},
    }
    manifest.update(overrides)
    return manifest


def _public_key(private_key: str) -> str:
    raw = base64.b64decode(private_key)
    key = Ed25519PrivateKey.from_private_bytes(raw).public_key()
    public = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(public).decode("ascii")


def test_validate_v2_manifest_normalizes_digest_alias_and_timestamp() -> None:
    manifest = _manifest()
    manifest["docker_digest"] = manifest.pop("image_digest")
    manifest["published_at"] = "2026-07-20T12:34:56+00:00"

    normalized = validate_update_manifest(manifest, require_signatures=False)

    assert normalized["image_digest"] == f"sha256:{'a' * 64}"
    assert "docker_digest" not in normalized
    assert normalized["published_at"] == "2026-07-20T12:34:56Z"
    assert normalized["compatibility"]["target_schema_revision"] == 2


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (lambda item: item.update(schema_version=1), "schema_version"),
        (lambda item: item.update(channel="nightly"), "channel"),
        (lambda item: item.update(version="2.1"), "SemVer"),
        (
            lambda item: item.update(docker_image="ghcr.io/example/wrong:2.1.0"),
            "exactly",
        ),
        (lambda item: item.update(image_digest="sha256:short"), "64 hex"),
        (lambda item: item["platform_digests"].pop("linux/arm64"), "exactly"),
        (lambda item: item.update(source_commit="abc123"), "40-character"),
        (lambda item: item.update(published_at="2026-07-20"), "RFC 3339"),
        (lambda item: item.update(sequence=True), "integer"),
        (lambda item: item.update(unexpected=True), "unknown field"),
        (
            lambda item: item["compatibility"].update(requires_backup=False),
            "requires_backup",
        ),
        (
            lambda item: item["compatibility"].update(target_schema_revision=0),
            "target_schema_revision",
        ),
        (
            lambda item: item["compatibility"].update(rollback_policy="automatic"),
            "rollback_policy",
        ),
        (
            lambda item: item["compatibility"].update(rollback_policy="image_only"),
            "rollback_policy",
        ),
    ],
)
def test_validate_v2_manifest_rejects_unsafe_or_ambiguous_metadata(
    mutation: object,
    error: str,
) -> None:
    manifest = _manifest()
    mutation(manifest)  # type: ignore[operator]

    with pytest.raises(ValueError, match=error):
        validate_update_manifest(manifest, require_signatures=False)


def test_channel_prerelease_rules_are_explicit() -> None:
    prerelease = _manifest(
        channel="beta",
        version="2.2.0-rc.1",
        docker_image="ghcr.io/ditaknet-sudo/ditaknet:2.2.0-rc.1",
        release_url=(
            "https://github.com/ditaknet-sudo/ditaknet/releases/tag/v2.2.0-rc.1"
        ),
    )
    assert (
        validate_update_manifest(prerelease, require_signatures=False)["channel"]
        == "beta"
    )

    stable_prerelease = {**prerelease, "channel": "stable"}
    with pytest.raises(ValueError, match="cannot publish a prerelease"):
        validate_update_manifest(stable_prerelease, require_signatures=False)

    beta_stable = _manifest(channel="beta")
    assert (
        validate_update_manifest(beta_stable, require_signatures=False)["version"]
        == "2.1.0"
    )


def test_digest_alias_must_not_conflict_with_canonical_digest() -> None:
    manifest = _manifest(docker_digest=f"sha256:{'e' * 64}")
    with pytest.raises(ValueError, match="disagree"):
        validate_update_manifest(manifest, require_signatures=False)


def test_canonical_payload_is_domain_separated_order_independent_and_unsigned() -> None:
    first = _manifest()
    second = dict(reversed(list(first.items())))
    signed = sign_manifest(first, key_id="stable-old", private_key=PRIVATE_KEY_ONE)

    assert canonical_manifest_payload(first).startswith(MANIFEST_DOMAIN + b"{")
    assert canonical_manifest_payload(first) == canonical_manifest_payload(second)
    assert canonical_manifest_payload(first) == canonical_manifest_payload(signed)
    assert b"signatures" not in canonical_manifest_payload(signed)


def test_ed25519_channel_keyring_supports_rotation_and_rejects_tampering() -> None:
    signed_once = sign_manifest(
        _manifest(), key_id="stable-2026a", private_key=PRIVATE_KEY_ONE
    )
    signed_twice = sign_manifest(
        signed_once, key_id="stable-2026b", private_key=PRIVATE_KEY_TWO
    )
    old_keyring = {"stable": {"stable-2026a": _public_key(PRIVATE_KEY_ONE)}}
    new_keyring = {"stable": {"stable-2026b": _public_key(PRIVATE_KEY_TWO)}}
    both_keyring = {"stable": {**old_keyring["stable"], **new_keyring["stable"]}}

    assert verify_manifest_signatures(signed_twice, old_keyring)
    assert verify_manifest_signatures(signed_twice, new_keyring)
    assert verify_manifest_signatures(
        signed_twice, both_keyring, minimum_valid_signatures=2
    )
    assert verified_signature_key_ids(signed_twice, both_keyring) == (
        "stable-2026a",
        "stable-2026b",
    )

    wrong_channel_keys = {"stable": {}, "beta": old_keyring["stable"]}
    assert not verify_manifest_signatures(signed_twice, wrong_channel_keys)

    tampered = deepcopy(signed_twice)
    tampered["sequence"] = 22
    assert not verify_manifest_signatures(tampered, both_keyring)


def test_signing_uses_raw_base64_key_and_published_manifest_requires_signature() -> (
    None
):
    with pytest.raises(ValueError, match="signatures is required"):
        validate_update_manifest(_manifest())
    with pytest.raises(ValueError, match="32 bytes"):
        sign_manifest(_manifest(), key_id="stable-bad", private_key="c2hvcnQ=")

    assert public_key_base64(PRIVATE_KEY_ONE) == _public_key(PRIVATE_KEY_ONE)


def test_release_manifest_cli_signs_and_verifies_without_printing_private_key(
    tmp_path: Path,
) -> None:
    source = tmp_path / "unsigned.json"
    signed_path = tmp_path / "signed.json"
    keyring_path = tmp_path / "keyring.json"
    source.write_text(json.dumps(_manifest()), encoding="utf-8")
    keyring_path.write_text(
        json.dumps({"stable": {"stable-test": _public_key(PRIVATE_KEY_ONE)}}),
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["TEST_UPDATE_PRIVATE_KEY"] = PRIVATE_KEY_ONE

    sign_result = subprocess.run(
        [
            sys.executable,
            "scripts/release_manifest.py",
            "sign",
            "--input",
            str(source),
            "--output",
            str(signed_path),
            "--key-id",
            "stable-test",
            "--private-key-env",
            "TEST_UPDATE_PRIVATE_KEY",
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert sign_result.returncode == 0, sign_result.stderr
    assert PRIVATE_KEY_ONE not in sign_result.stdout + sign_result.stderr
    signed = json.loads(signed_path.read_text(encoding="utf-8"))
    assert signed["signatures"][0]["key_id"] == "stable-test"

    verify_result = subprocess.run(
        [
            sys.executable,
            "scripts/release_manifest.py",
            "verify",
            "--input",
            str(signed_path),
            "--keyring",
            str(keyring_path),
            "--channel",
            "stable",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert verify_result.returncode == 0, verify_result.stderr
    assert "verified stable manifest 2.1.0" in verify_result.stdout


def test_release_manifest_cli_builds_digest_bound_metadata(tmp_path: Path) -> None:
    policy = tmp_path / "policy.json"
    notes = tmp_path / "notes.md"
    output = tmp_path / "unsigned.json"
    policy.write_text(
        json.dumps(_manifest()["compatibility"]),
        encoding="utf-8",
    )
    notes.write_text("# DitakNet 2.1.0\n\nComplete notes.\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/release_manifest.py",
            "build",
            "--version",
            "2.1.0",
            "--channel",
            "stable",
            "--image-digest",
            f"sha256:{'a' * 64}",
            "--amd64-digest",
            f"sha256:{'b' * 64}",
            "--arm64-digest",
            f"sha256:{'c' * 64}",
            "--source-commit",
            "d" * 40,
            "--published-at",
            "2026-07-20T12:34:56Z",
            "--sequence",
            "21",
            "--policy",
            str(policy),
            "--release-notes-file",
            str(notes),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    built = validate_update_manifest(
        json.loads(output.read_text(encoding="utf-8")),
        require_signatures=False,
    )
    assert built["image_digest"] == f"sha256:{'a' * 64}"
    assert built["platform_digests"]["linux/arm64"] == f"sha256:{'c' * 64}"
    assert built["release_notes"] == "# DitakNet 2.1.0\n\nComplete notes."
    assert built["changelog_url"].endswith("/release/notes/2.1.0.md")
    assert "signatures" not in built
