#!/usr/bin/env python3
"""Fail CI when source/build versions or live release metadata are invalid.

This script intentionally uses only the Python standard library so it can run
before application dependencies are installed. ``VERSION`` is the canonical
source/build version. The update manifest is independently published live
metadata: it may lag the source, but it must be internally consistent and must
never advertise a version newer than the source. A tag or manual release passes
its resolved version via ``--expected`` and must match ``VERSION`` exactly.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
SOURCE_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-(?:beta|rc)\.[0-9]+)?")
IMAGE = "ghcr.io/ditaknet-sudo/ditaknet"


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _capture(relative_path: str, pattern: str, label: str) -> tuple[str, str]:
    match = re.search(pattern, _read(relative_path), re.MULTILINE | re.DOTALL)
    if not match:
        raise ValueError(f"{label}: value not found in {relative_path}")
    return label, match.group(1).strip()


def _env_value(relative_path: str, key: str, label: str) -> tuple[str, str]:
    return _capture(
        relative_path,
        rf"^{re.escape(key)}=([^\s#]+)\s*$",
        label,
    )


def _manifest_value(manifest: dict[str, Any], key: str) -> str:
    value = manifest.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"update-manifest.json: {key!r} must be a non-empty string")
    return value.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--expected",
        help="Exact stable SemVer expected by the release ref (for example 2.0.2)",
    )
    args = parser.parse_args()

    errors: list[str] = []
    try:
        canonical = _read("VERSION").strip()
        manifest = json.loads(_read("update-manifest.json"))
        if not isinstance(manifest, dict):
            raise ValueError("update-manifest.json: top-level value must be an object")
        manifest_version = _manifest_value(manifest, "latest_version")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"release validation failed: {exc}", file=sys.stderr)
        return 1

    expected = (args.expected or canonical).strip()
    if not SOURCE_VERSION.fullmatch(canonical):
        errors.append(
            f"VERSION {canonical!r} must be X.Y.Z, X.Y.Z-beta.N, or X.Y.Z-rc.N"
        )
    if not SOURCE_VERSION.fullmatch(expected):
        errors.append(
            f"expected version {expected!r} must be X.Y.Z, X.Y.Z-beta.N, or X.Y.Z-rc.N"
        )
    if expected != canonical:
        errors.append(f"VERSION: expected {expected!r}, found {canonical!r}")
    if not SEMVER.fullmatch(manifest_version):
        errors.append(
            "manifest latest_version must be stable SemVer X.Y.Z "
            f"(found {manifest_version!r})"
        )

    checks: list[tuple[str, str]] = []
    try:
        checks.extend(
            (
                _capture(
                    "ditaknet/config.py",
                    r'^\s*app_version:\s*str\s*=\s*Field\(default="([^"]+)"',
                    "application default",
                ),
                _capture(
                    "Dockerfile",
                    r"^ARG APP_VERSION=([^\s#]+)\s*$",
                    "Dockerfile APP_VERSION",
                ),
                _capture(
                    "Dockerfile",
                    r"^ARG IMAGE_TAG=([^\s#]+)\s*$",
                    "Dockerfile IMAGE_TAG",
                ),
                _env_value("config/runtime.env", "APP_VERSION", "runtime APP_VERSION"),
                _env_value("config/runtime.env", "IMAGE_TAG", "runtime IMAGE_TAG"),
                _capture(
                    "docker-compose.yml",
                    r"image:\s*ghcr\.io/ditaknet-sudo/ditaknet:\$\{DITAKNET_VERSION:-([^}]+)\}",
                    "Docker Compose image fallback",
                ),
                _capture(
                    "docker-compose.yml",
                    r"APP_VERSION:\s*\$\{DITAKNET_VERSION:-([^}]+)\}",
                    "Docker Compose runtime APP_VERSION fallback",
                ),
                _capture(
                    "docker-compose.yml",
                    r"IMAGE_TAG:\s*\$\{DITAKNET_VERSION:-([^}]+)\}",
                    "Docker Compose runtime IMAGE_TAG fallback",
                ),
                _capture(
                    "truenas/docker-compose.yml",
                    r"image:\s*ghcr\.io/ditaknet-sudo/ditaknet:\$\{DITAKNET_VERSION:-([^}]+)\}",
                    "TrueNAS bridge image fallback",
                ),
                _capture(
                    "truenas/docker-compose.host-network.yml",
                    r"image:\s*ghcr\.io/ditaknet-sudo/ditaknet:\$\{DITAKNET_VERSION:-([^}]+)\}",
                    "TrueNAS host image fallback",
                ),
                _env_value(
                    "truenas/.env.example",
                    "DITAKNET_VERSION",
                    "TrueNAS env image version",
                ),
                _capture(
                    "truenas-catalog/ix-dev/community/ditaknet/app.yaml",
                    r'^app_version:\s*["\']?([^"\'\s]+)["\']?\s*$',
                    "TrueNAS catalog app_version",
                ),
                _capture(
                    "truenas-catalog/ix-dev/community/ditaknet/ix_values.yaml",
                    r'^\s*tag:\s*["\']?([^"\'\s]+)["\']?\s*$',
                    "TrueNAS catalog image tag",
                ),
                _capture(
                    "README.md",
                    r"Current app version:\s*\*\*([^*]+)\*\*",
                    "README current version",
                ),
                _capture(
                    "CHANGELOG.md",
                    r"^##\s+\[([0-9]+\.[0-9]+\.[0-9]+(?:-(?:beta|rc)\.[0-9]+)?)\]",
                    "CHANGELOG newest version",
                ),
            )
        )
    except (OSError, UnicodeError, ValueError) as exc:
        errors.append(str(exc))

    for label, actual in checks:
        if actual != expected:
            errors.append(f"{label}: expected {expected!r}, found {actual!r}")

    try:
        release_notes_path = f"release/notes/{canonical}.md"
        release_notes = _read(release_notes_path)
        expected_heading = f"# DitakNet {canonical}"
        first_line = release_notes.splitlines()[0] if release_notes else ""
        if first_line != expected_heading:
            errors.append(
                f"{release_notes_path}: first line must be {expected_heading!r}"
            )
        for required_heading in (
            "## Highlights",
            "## Upgrade from",
            "## Validation scope",
        ):
            if required_heading not in release_notes:
                errors.append(
                    f"{release_notes_path}: missing required section "
                    f"{required_heading!r}"
                )
    except (OSError, UnicodeError, IndexError) as exc:
        errors.append(f"versioned release notes: {exc}")

    manifest_expected = {
        "manifest version": manifest_version,
        "manifest image_tag": manifest_version,
        "manifest docker_image": f"{IMAGE}:{manifest_version}",
        "manifest image": f"{IMAGE}:{manifest_version}",
    }
    for label, wanted in manifest_expected.items():
        key = label.removeprefix("manifest ")
        try:
            actual = _manifest_value(manifest, key)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if actual != wanted:
            errors.append(f"{label}: expected {wanted!r}, found {actual!r}")

    release_url = str(manifest.get("release_url") or "").strip()
    release_page = (
        f"https://github.com/ditaknet-sudo/ditaknet/releases/tag/v{manifest_version}"
    )
    tag_page = f"https://github.com/ditaknet-sudo/ditaknet/tree/v{manifest_version}"
    allowed_release_urls = {release_page}
    if int(manifest.get("schema_version") or 1) == 1:
        # The historical v2.0.1 artifact predates GitHub Releases. Keep its
        # live legacy pointer accurate instead of linking to a nonexistent
        # release; signed schema-v2 metadata must use a real Release page.
        allowed_release_urls.add(tag_page)
    if release_url not in allowed_release_urls:
        errors.append(
            "manifest release_url: expected one of "
            f"{sorted(allowed_release_urls)!r}, found {release_url!r}"
        )

    if SOURCE_VERSION.fullmatch(canonical) and SEMVER.fullmatch(manifest_version):
        source_tuple = tuple(map(int, canonical.split("-", 1)[0].split(".")))
        manifest_tuple = tuple(map(int, manifest_version.split(".")))
        if manifest_tuple > source_tuple:
            errors.append(
                "manifest latest_version cannot advertise a version newer than "
                f"VERSION ({manifest_version} > {canonical})"
            )

    if manifest.get("channel") != "stable":
        errors.append("manifest channel: release workflow requires 'stable'")

    minimum_supported = manifest.get("minimum_supported_version")
    if not isinstance(minimum_supported, str) or not SEMVER.fullmatch(
        minimum_supported
    ):
        errors.append("manifest minimum_supported_version must be stable SemVer X.Y.Z")
    elif SEMVER.fullmatch(manifest_version) and tuple(
        map(int, minimum_supported.split("."))
    ) > tuple(map(int, manifest_version.split("."))):
        errors.append("manifest minimum_supported_version cannot exceed latest_version")

    try:
        policy = json.loads(_read("release/update-policy.json"))
        keyring = json.loads(_read("ditaknet/core/update_signing_public_keys.json"))
        if not isinstance(policy, dict):
            raise ValueError("release/update-policy.json must contain an object")
        expected_policy_fields = {
            "minimum_current_version",
            "maximum_current_version",
            "requires_backup",
            "allow_major_upgrade",
            "target_schema_revision",
            "backup_format_version",
            "rollback_policy",
        }
        if set(policy) != expected_policy_fields:
            errors.append("release update policy fields do not match schema v2")
        minimum_current = str(policy.get("minimum_current_version") or "")
        maximum_current = str(policy.get("maximum_current_version") or "")
        if not SEMVER.fullmatch(minimum_current) or not SEMVER.fullmatch(
            maximum_current
        ):
            errors.append("release update policy version bounds must be stable SemVer")
        else:
            minimum_tuple = tuple(map(int, minimum_current.split(".")))
            maximum_tuple = tuple(map(int, maximum_current.split(".")))
            source_tuple = tuple(map(int, canonical.split("-", 1)[0].split(".")))
            if not minimum_tuple <= maximum_tuple <= source_tuple:
                errors.append(
                    "release update policy must satisfy minimum <= maximum <= VERSION"
                )
        if policy.get("requires_backup") is not True:
            errors.append("release update policy must require backup")
        if policy.get("rollback_policy") != "state_restore_required":
            errors.append("release update policy must require state-aware rollback")

        schema_revision = int(
            _capture(
                "ditaknet/database.py",
                r"^DATABASE_SCHEMA_REVISION\s*=\s*(\d+)\s*$",
                "database schema revision",
            )[1]
        )
        backup_format = int(
            _capture(
                "ditaknet/core/backup.py",
                r"^FORMAT_VERSION\s*=\s*(\d+)\s*$",
                "backup format version",
            )[1]
        )
        if policy.get("target_schema_revision") != schema_revision:
            errors.append("release update policy schema revision is stale")
        if policy.get("backup_format_version") != backup_format:
            errors.append("release update policy backup format is stale")

        if not isinstance(keyring, dict) or set(keyring) != {"stable", "beta"}:
            errors.append("update public keyring must contain stable and beta objects")
        else:
            for channel, keys in keyring.items():
                if not isinstance(keys, dict):
                    errors.append(
                        f"update public keyring {channel} value must be an object"
                    )
                    continue
                for key_id, value in keys.items():
                    if not isinstance(key_id, str) or not key_id.startswith(
                        f"{channel}-"
                    ):
                        errors.append(
                            f"update public key ID is not channel-scoped: {key_id!r}"
                        )
                        continue
                    try:
                        decoded = base64.b64decode(str(value), validate=True)
                    except (ValueError, binascii.Error):
                        decoded = b""
                    if (
                        len(decoded) != 32
                        or base64.b64encode(decoded).decode("ascii") != value
                    ):
                        errors.append(
                            f"update public key is invalid: {channel}/{key_id}"
                        )
            release_channel = "beta" if "-" in canonical else "stable"
            release_key_id = f"{release_channel}-release-v1"
            if not (keyring.get(release_channel) or {}).get(release_key_id):
                errors.append(
                    "release public key is not provisioned: "
                    f"{release_channel}/{release_key_id}"
                )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        errors.append(f"release security metadata: {exc}")

    if errors:
        print("Release/version consistency validation FAILED:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"Release/version consistency OK: {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
