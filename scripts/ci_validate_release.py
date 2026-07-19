#!/usr/bin/env python3
"""Fail CI when release/version sources disagree.

This script intentionally uses only the Python standard library so it can run
before application dependencies are installed. The update manifest is the
canonical source for branch/PR validation; a tag or manual release passes its
resolved version via ``--expected``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
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
        help="Exact stable SemVer expected by the release ref (for example 2.0.1)",
    )
    args = parser.parse_args()

    errors: list[str] = []
    try:
        manifest = json.loads(_read("update-manifest.json"))
        if not isinstance(manifest, dict):
            raise ValueError("update-manifest.json: top-level value must be an object")
        canonical = _manifest_value(manifest, "latest_version")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"release validation failed: {exc}", file=sys.stderr)
        return 1

    expected = (args.expected or canonical).strip()
    if not SEMVER.fullmatch(expected):
        errors.append(
            f"expected version {expected!r} is not stable SemVer X.Y.Z "
            "(prerelease/floating tags are not publishable)"
        )

    checks: list[tuple[str, str]] = []
    try:
        checks.extend(
            (
                ("manifest latest_version", canonical),
                ("manifest version", _manifest_value(manifest, "version")),
                ("manifest image_tag", _manifest_value(manifest, "image_tag")),
                ("manifest docker_image", _manifest_value(manifest, "docker_image")),
                ("manifest image", _manifest_value(manifest, "image")),
                ("manifest release_url", _manifest_value(manifest, "release_url")),
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
                    r"APP_VERSION:\s*\$\{APP_VERSION:-([^}]+)\}",
                    "Docker Compose APP_VERSION fallback",
                ),
                _capture(
                    "docker-compose.yml",
                    r"IMAGE_TAG:\s*\$\{IMAGE_TAG:-([^}]+)\}",
                    "Docker Compose IMAGE_TAG fallback",
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
                    "truenas-catalog/ix-dev/community/ditaknet/questions.yaml",
                    r'variable:\s*image_tag.*?default:\s*["\']?([^"\'\s]+)["\']?\s*$',
                    "TrueNAS catalog question default",
                ),
                _capture(
                    "truenas-catalog/ix-dev/community/ditaknet/templates/test_values/basic-values.yaml",
                    r'^\s*image_tag:\s*["\']?([^"\'\s]+)["\']?\s*$',
                    "TrueNAS catalog test image tag",
                ),
                _capture(
                    "README.md",
                    r"Current app version:\s*\*\*([^*]+)\*\*",
                    "README current version",
                ),
                _capture(
                    "CHANGELOG.md",
                    r"^##\s+\[([0-9]+\.[0-9]+\.[0-9]+)\]",
                    "CHANGELOG newest version",
                ),
            )
        )
    except (OSError, UnicodeError, ValueError) as exc:
        errors.append(str(exc))

    expected_image = f"{IMAGE}:{expected}"
    expected_release_url = (
        f"https://github.com/ditaknet-sudo/ditaknet/releases/tag/v{expected}"
    )
    special_expected = {
        "manifest docker_image": expected_image,
        "manifest image": expected_image,
        "manifest release_url": expected_release_url,
    }

    for label, actual in checks:
        wanted = special_expected.get(label, expected)
        if actual != wanted:
            errors.append(f"{label}: expected {wanted!r}, found {actual!r}")

    if manifest.get("channel") != "stable":
        errors.append("manifest channel: release workflow requires 'stable'")

    minimum_supported = manifest.get("minimum_supported_version")
    if not isinstance(minimum_supported, str) or not SEMVER.fullmatch(minimum_supported):
        errors.append("manifest minimum_supported_version must be stable SemVer X.Y.Z")
    elif SEMVER.fullmatch(expected) and tuple(
        map(int, minimum_supported.split("."))
    ) > tuple(map(int, expected.split("."))):
        errors.append("manifest minimum_supported_version cannot exceed latest_version")

    if errors:
        print("Release/version consistency validation FAILED:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print(f"Release/version consistency OK: {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
