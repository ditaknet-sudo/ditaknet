"""Release metadata and deployment-template consistency checks."""

from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

from scripts import ci_validate_release
from ditaknet import database
from ditaknet.core import backup


ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
SOURCE_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:-(?:beta|rc)\.\d+)?$")


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _capture(relative: str, pattern: str) -> str:
    match = re.search(pattern, _read(relative), flags=re.MULTILINE)
    assert match, f"Could not find version in {relative}"
    return match.group(1)


def test_all_release_version_sources_match() -> None:
    expected = _read("VERSION").strip()

    assert SOURCE_VERSION.fullmatch(expected)

    versions = {
        "Dockerfile APP_VERSION": _capture("Dockerfile", r"^ARG APP_VERSION=([^\s]+)$"),
        "Dockerfile IMAGE_TAG": _capture("Dockerfile", r"^ARG IMAGE_TAG=([^\s]+)$"),
        "runtime APP_VERSION": _capture(
            "config/runtime.env", r"^APP_VERSION=([^\s]+)$"
        ),
        "runtime IMAGE_TAG": _capture("config/runtime.env", r"^IMAGE_TAG=([^\s]+)$"),
        "environment example": _capture(".env.example", r"^# APP_VERSION=([^\s]+)$"),
        "Python settings": _capture(
            "ditaknet/config.py",
            r'app_version:\s*str\s*=\s*Field\(default="([^"]+)"',
        ),
        "README": _capture(
            "README.md", r"^Current app version:\s*\*\*([^*]+)\*\*\.\s*$"
        ),
    }

    assert versions == {name: expected for name in versions}, versions


def test_production_templates_do_not_default_to_latest() -> None:
    expected = _read("VERSION").strip()

    for relative in (
        "docker-compose.yml",
        "truenas/docker-compose.yml",
        "truenas/docker-compose.host-network.yml",
    ):
        content = _read(relative)
        assert ":-latest}" not in content, relative
        assert expected in content, relative


def test_container_deployments_force_production_environment() -> None:
    assert re.search(r"^APP_ENV=production$", _read("config/runtime.env"), re.MULTILINE)
    assert re.search(r"^\s*APP_ENV=production\s*\\$", _read("Dockerfile"), re.MULTILINE)
    assert re.search(
        r'app_env:\s*str\s*=\s*Field\(default="production"',
        _read("ditaknet/config.py"),
    )

    for relative in (
        "truenas/docker-compose.yml",
        "truenas/docker-compose.host-network.yml",
    ):
        assert re.search(
            r'^\s+APP_ENV:\s*["\']?production["\']?\s*$',
            _read(relative),
            re.MULTILINE,
        ), relative

    catalog_template = _read(
        "truenas-catalog/ix-dev/community/ditaknet/templates/docker-compose.yaml"
    )
    assert 'add_env("APP_ENV", "production")' in catalog_template


def test_public_manifest_uses_stable_exact_image() -> None:
    manifest = json.loads(_read("update-manifest.json"))
    source_version = _read("VERSION").strip()
    latest_version = manifest["latest_version"]

    assert manifest["channel"] == "stable"
    assert manifest["critical"] is False
    assert SOURCE_VERSION.fullmatch(source_version)
    assert SEMVER.fullmatch(latest_version)
    assert manifest["version"] == latest_version
    assert manifest["image_tag"] == latest_version
    expected_image = f"ghcr.io/ditaknet-sudo/ditaknet:{latest_version}"
    assert manifest["docker_image"] == expected_image
    assert manifest["image"] == expected_image
    assert manifest["release_url"] in {
        f"https://github.com/ditaknet-sudo/ditaknet/releases/tag/v{latest_version}",
        f"https://github.com/ditaknet-sudo/ditaknet/tree/v{latest_version}",
    }
    assert ":latest" not in manifest["docker_image"]
    assert manifest["minimum_supported_version"]
    assert SEMVER.fullmatch(manifest["minimum_supported_version"])
    source = tuple(int(part) for part in source_version.split("."))
    latest = tuple(int(part) for part in latest_version.split("."))
    minimum = tuple(
        int(part) for part in manifest["minimum_supported_version"].split(".")
    )
    assert minimum <= latest
    assert latest <= source


def _manifest_at(version: str) -> str:
    manifest = json.loads(_read("update-manifest.json"))
    manifest.update(
        {
            "latest_version": version,
            "version": version,
            "image_tag": version,
            "docker_image": f"ghcr.io/ditaknet-sudo/ditaknet:{version}",
            "image": f"ghcr.io/ditaknet-sudo/ditaknet:{version}",
            "release_url": (
                f"https://github.com/ditaknet-sudo/ditaknet/releases/tag/v{version}"
            ),
            "minimum_supported_version": version,
        }
    )
    return json.dumps(manifest)


def _next_source_version() -> str:
    major, minor, patch = map(int, _read("VERSION").strip().split("."))
    return f"{major}.{minor}.{patch + 1}"


def test_release_validator_allows_live_manifest_to_lag_source(
    monkeypatch, capsys
) -> None:
    original_read = ci_validate_release._read
    monkeypatch.setattr(
        ci_validate_release,
        "_read",
        lambda relative: (
            _manifest_at("2.0.0")
            if relative == "update-manifest.json"
            else original_read(relative)
        ),
    )
    monkeypatch.setattr(sys, "argv", ["ci_validate_release.py"])

    assert ci_validate_release.main() == 0
    assert (
        f"Release/version consistency OK: {_read('VERSION').strip()}"
        in capsys.readouterr().out
    )


def test_release_validator_rejects_live_manifest_newer_than_source(
    monkeypatch, capsys
) -> None:
    original_read = ci_validate_release._read
    future_version = _next_source_version()
    monkeypatch.setattr(
        ci_validate_release,
        "_read",
        lambda relative: (
            _manifest_at(future_version)
            if relative == "update-manifest.json"
            else original_read(relative)
        ),
    )
    monkeypatch.setattr(sys, "argv", ["ci_validate_release.py"])

    assert ci_validate_release.main() == 1
    assert "cannot advertise a version newer than VERSION" in capsys.readouterr().err


def test_release_validator_rejects_release_ref_that_differs_from_source(
    monkeypatch, capsys
) -> None:
    source_version = _read("VERSION").strip()
    future_version = _next_source_version()
    monkeypatch.setattr(
        sys,
        "argv",
        ["ci_validate_release.py", "--expected", future_version],
    )

    assert ci_validate_release.main() == 1
    assert (
        f"VERSION: expected {future_version!r}, found {source_version!r}"
        in capsys.readouterr().err
    )


def _load_json_without_ambiguous_keys(path: Path) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        seen_casefolded: dict[str, str] = {}
        for key, value in pairs:
            folded = key.casefold()
            if key in result:
                raise AssertionError(f"Duplicate JSON key {key!r} in {path.name}")
            if folded in seen_casefolded:
                previous = seen_casefolded[folded]
                raise AssertionError(
                    f"Case-ambiguous JSON keys {previous!r}/{key!r} in {path.name}"
                )
            result[key] = value
            seen_casefolded[folded] = key
        return result

    return json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates
    )


def test_public_json_files_have_no_duplicate_or_case_ambiguous_keys() -> None:
    paths = [
        ROOT / "update-manifest.json",
        ROOT / "release/update-policy.json",
        ROOT / "ditaknet/core/update_signing_public_keys.json",
        *sorted((ROOT / "app/i18n").glob("*.json")),
    ]

    for path in paths:
        _load_json_without_ambiguous_keys(path)


def test_all_locales_expose_the_same_translation_keys() -> None:
    locale_files = sorted((ROOT / "app/i18n").glob("*.json"))
    locale_keys = {
        path.name: set(_load_json_without_ambiguous_keys(path)) for path in locale_files
    }
    expected = locale_keys["en.json"]
    differences = {
        name: sorted(keys.symmetric_difference(expected))
        for name, keys in locale_keys.items()
        if keys != expected
    }

    assert differences == {}, differences


def test_release_policy_matches_runtime_compatibility_contract() -> None:
    policy = _load_json_without_ambiguous_keys(ROOT / "release/update-policy.json")
    assert set(policy) == {
        "minimum_current_version",
        "maximum_current_version",
        "requires_backup",
        "allow_major_upgrade",
        "target_schema_revision",
        "backup_format_version",
        "rollback_policy",
    }
    assert policy["requires_backup"] is True
    assert isinstance(policy["allow_major_upgrade"], bool)
    assert policy["target_schema_revision"] == database.DATABASE_SCHEMA_REVISION
    assert policy["backup_format_version"] == backup.FORMAT_VERSION
    assert policy["rollback_policy"] == "state_restore_required"

    minimum = tuple(map(int, str(policy["minimum_current_version"]).split(".")))
    maximum = tuple(map(int, str(policy["maximum_current_version"]).split(".")))
    target = tuple(map(int, _read("VERSION").strip().split("-", 1)[0].split(".")))
    assert minimum <= maximum <= target


def test_committed_update_keyring_contains_public_channel_keys_only() -> None:
    path = ROOT / "ditaknet/core/update_signing_public_keys.json"
    keyring = _load_json_without_ambiguous_keys(path)
    assert set(keyring) == {"stable", "beta"}
    assert "private" not in path.read_text(encoding="utf-8").casefold()
    assert "secret" not in path.read_text(encoding="utf-8").casefold()

    for channel, raw_keys in keyring.items():
        assert isinstance(raw_keys, dict)
        for key_id, public_key in raw_keys.items():
            assert key_id.startswith(f"{channel}-")
            assert isinstance(public_key, str)
            decoded = base64.b64decode(public_key, validate=True)
            assert len(decoded) == 32
            assert base64.b64encode(decoded).decode("ascii") == public_key


def test_release_workflow_orders_trust_gates_and_feed_promotion() -> None:
    workflow = _read(".github/workflows/publish-ghcr.yml")
    key_gate = workflow.index("Verify protected channel signing key")
    registry_push = workflow.index("Publish exact per-architecture images")
    signed_metadata = workflow.index(
        "Build, sign and verify digest-bound update metadata"
    )
    final_tag = workflow.index("Finalize the immutable SemVer release tag")
    github_release = workflow.index("Create or safely resume the GitHub Release")
    feed_promotion = workflow.index("Atomically promote the selected signed channel")

    assert key_gate < registry_push < signed_metadata < final_tag
    assert final_tag < github_release < feed_promotion
    assert 'SEQUENCE="$(git rev-list --count "$SOURCE_COMMIT")"' in workflow
    assert "equal channel sequence reused for different metadata" in workflow
    assert "higher channel sequence must publish a newer version" in workflow
    assert "--bundle-from-oci" in workflow
    assert '--source-digest "$SOURCE_COMMIT"' in workflow
    assert "len(manifests) == 2" in workflow
    assert "Manual dispatch is metadata repair only" in workflow
