"""Release metadata and deployment-template consistency checks."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _capture(relative: str, pattern: str) -> str:
    match = re.search(pattern, _read(relative), flags=re.MULTILINE)
    assert match, f"Could not find version in {relative}"
    return match.group(1)


def test_all_release_version_sources_match() -> None:
    manifest = json.loads(_read("update-manifest.json"))
    expected = manifest["latest_version"]

    assert SEMVER.fullmatch(expected)
    assert manifest["version"] == expected
    assert manifest["image_tag"] == expected
    expected_image = f"ghcr.io/ditaknet-sudo/ditaknet:{expected}"
    expected_release = (
        f"https://github.com/ditaknet-sudo/ditaknet/releases/tag/v{expected}"
    )
    assert manifest["docker_image"] == expected_image
    assert manifest["image"] == expected_image
    assert manifest["release_url"] == expected_release

    versions = {
        "Dockerfile APP_VERSION": _capture(
            "Dockerfile", r"^ARG APP_VERSION=([^\s]+)$"
        ),
        "Dockerfile IMAGE_TAG": _capture(
            "Dockerfile", r"^ARG IMAGE_TAG=([^\s]+)$"
        ),
        "runtime APP_VERSION": _capture(
            "config/runtime.env", r"^APP_VERSION=([^\s]+)$"
        ),
        "runtime IMAGE_TAG": _capture(
            "config/runtime.env", r"^IMAGE_TAG=([^\s]+)$"
        ),
        "environment example": _capture(
            ".env.example", r"^# APP_VERSION=([^\s]+)$"
        ),
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
    expected = json.loads(_read("update-manifest.json"))["latest_version"]

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

    assert manifest["channel"] == "stable"
    assert manifest["critical"] is False
    assert ":latest" not in manifest["docker_image"]
    assert manifest["minimum_supported_version"]
    assert SEMVER.fullmatch(manifest["minimum_supported_version"])
    latest = tuple(int(part) for part in manifest["latest_version"].split("."))
    minimum = tuple(
        int(part) for part in manifest["minimum_supported_version"].split(".")
    )
    assert minimum <= latest


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

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates)


def test_public_json_files_have_no_duplicate_or_case_ambiguous_keys() -> None:
    paths = [ROOT / "update-manifest.json", *sorted((ROOT / "app/i18n").glob("*.json"))]

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
