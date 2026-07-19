#!/usr/bin/env python3
"""Validate DitakNet's TrueNAS Custom App and upstream catalog package.

The default gate is deterministic and offline. ``--check-upstream`` also
checks the pinned library lock against TrueNAS' official ``library/hashes.yaml``.
``--compose-config`` asks Docker Compose to normalize both Custom App files
without creating containers.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
CATALOG = Path("truenas-catalog/ix-dev/community/ditaknet")
IMAGE_REPOSITORY = "ghcr.io/ditaknet-sudo/ditaknet"
OFFICIAL_CATALOG = "https://github.com/truenas/apps.git"
OFFICIAL_COMMIT = "f733713ecfda1d683043775e6d9cc8f09545e1b3"
OFFICIAL_HASHES = (
    f"https://raw.githubusercontent.com/truenas/apps/{OFFICIAL_COMMIT}/library/hashes.yaml"
)
SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
WRITABLE_TARGETS = {"/app/data", "/app/logs", "/app/backups", "/app/plugins"}


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _read(relative: str | Path) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _yaml(relative: str | Path) -> Any:
    return yaml.load(_read(relative), Loader=UniqueKeyLoader)


def _json(relative: str | Path) -> Any:
    return json.loads(_read(relative))


def _question_variables(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    questions = document.get("questions", [])
    if not isinstance(questions, list):
        return {}
    return {
        item.get("variable"): item
        for item in questions
        if isinstance(item, dict) and isinstance(item.get("variable"), str)
    }


def _attrs(question: dict[str, Any]) -> dict[str, dict[str, Any]]:
    schema = question.get("schema", {})
    attrs = schema.get("attrs", []) if isinstance(schema, dict) else []
    return {
        item.get("variable"): item
        for item in attrs
        if isinstance(item, dict) and isinstance(item.get("variable"), str)
    }


def _validate_compose(relative: str, *, host_network: bool, errors: list[str]) -> None:
    try:
        document = _yaml(relative)
        service = document["services"]["ditaknet"]
    except (OSError, UnicodeError, ValueError, yaml.YAMLError, KeyError, TypeError) as exc:
        errors.append(f"{relative}: cannot load DitakNet service: {exc}")
        return

    expected = {
        "user": "568:568",
        "read_only": True,
        "privileged": False,
        "init": True,
    }
    for key, wanted in expected.items():
        if service.get(key) != wanted:
            errors.append(f"{relative}: {key} must be {wanted!r}")

    if service.get("cap_drop") != ["ALL"]:
        errors.append(f"{relative}: cap_drop must contain only ALL")
    if service.get("cap_add") != ["NET_RAW"]:
        errors.append(f"{relative}: cap_add must contain only NET_RAW")
    if "no-new-privileges=true" not in service.get("security_opt", []):
        errors.append(f"{relative}: no-new-privileges is required")
    if service.get("pids_limit", 0) <= 0:
        errors.append(f"{relative}: a positive pids_limit is required")
    if "container_name" in service:
        errors.append(f"{relative}: fixed container_name breaks Compose project isolation")
    if service.get("environment", {}).get("APP_ENV") != "production":
        errors.append(f"{relative}: APP_ENV must be production")

    image = service.get("image", "")
    if not re.fullmatch(
        rf"{re.escape(IMAGE_REPOSITORY)}:\$\{{DITAKNET_VERSION:-[0-9]+\.[0-9]+\.[0-9]+\}}",
        image,
    ):
        errors.append(f"{relative}: image must use an exact SemVer fallback")

    if host_network:
        if service.get("network_mode") != "host":
            errors.append(f"{relative}: host variant must set network_mode: host")
        if "ports" in service:
            errors.append(f"{relative}: host variant must not publish ports")
    else:
        if "network_mode" in service:
            errors.append(f"{relative}: bridge variant must not set network_mode")
        if len(service.get("ports", [])) != 1:
            errors.append(f"{relative}: bridge variant must publish one WebUI port")

    mounts = service.get("volumes", [])
    targets: set[str] = set()
    for mount in mounts:
        if not isinstance(mount, dict):
            errors.append(f"{relative}: persistent mounts must use long bind syntax")
            continue
        target = mount.get("target")
        if isinstance(target, str):
            targets.add(target)
        if mount.get("type") != "bind":
            errors.append(f"{relative}: {target!r} must be a bind mount")
        if mount.get("bind", {}).get("create_host_path") is not False:
            errors.append(f"{relative}: {target!r} must fail if its dataset is missing")
    if targets != WRITABLE_TARGETS:
        errors.append(f"{relative}: persistent targets are {sorted(targets)!r}")

    tmpfs = service.get("tmpfs", [])
    if not any(isinstance(item, str) and item.startswith("/tmp:") for item in tmpfs):
        errors.append(f"{relative}: writable /tmp tmpfs is required with read-only rootfs")


def validate(*, check_upstream: bool = False, compose_config: bool = False) -> list[str]:
    errors: list[str] = []

    yaml_files = [
        "truenas/docker-compose.yml",
        "truenas/docker-compose.host-network.yml",
        CATALOG / "app.yaml",
        CATALOG / "item.yaml",
        CATALOG / "ix_values.yaml",
        CATALOG / "questions.yaml",
        *sorted((ROOT / CATALOG / "templates/test_values").glob("*.yaml")),
    ]
    for path in yaml_files:
        try:
            document = _yaml(path.relative_to(ROOT) if Path(path).is_absolute() else path)
            if not isinstance(document, dict):
                errors.append(f"{path}: top-level YAML value must be a mapping")
        except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
            errors.append(f"{path}: invalid YAML: {exc}")

    _validate_compose("truenas/docker-compose.yml", host_network=False, errors=errors)
    _validate_compose(
        "truenas/docker-compose.host-network.yml", host_network=True, errors=errors
    )

    try:
        manifest = _json("update-manifest.json")
        version = manifest["latest_version"]
        if not isinstance(version, str) or not SEMVER.fullmatch(version):
            raise ValueError("latest_version is not stable SemVer")
        app = _yaml(CATALOG / "app.yaml")
        values = _yaml(CATALOG / "ix_values.yaml")
        lock = _json("truenas-catalog/upstream-library.json")

        if app.get("app_version") != version:
            errors.append("catalog app_version does not match update-manifest.json")
        image = values.get("images", {}).get("image", {})
        if image != {"repository": IMAGE_REPOSITORY, "tag": version}:
            errors.append("catalog image repository/tag does not match the stable release")
        if app.get("lib_version") != lock.get("version"):
            errors.append("catalog lib_version does not match upstream-library.json")
        if app.get("lib_version_hash") != lock.get("sha256"):
            errors.append("catalog lib_version_hash does not match upstream-library.json")
        if lock.get("catalog_repository") != OFFICIAL_CATALOG:
            errors.append("catalog library lock repository is not the official truenas/apps repo")
        if lock.get("catalog_commit") != OFFICIAL_COMMIT:
            errors.append("catalog library lock commit differs from the reviewed official commit")
        if lock.get("source") != OFFICIAL_HASHES:
            errors.append("catalog library hash source must be pinned to the reviewed commit")
        if not re.fullmatch(r"[0-9a-f]{64}", str(app.get("lib_version_hash", ""))):
            errors.append("catalog lib_version_hash must be a lowercase SHA-256")
        contexts = app.get("run_as_context", [])
        if not contexts or contexts[0].get("uid") != 568 or contexts[0].get("gid") != 568:
            errors.append("catalog run_as_context must document UID/GID 568")
        if app.get("categories") != ["monitoring"]:
            errors.append("catalog app.yaml must use exactly one upstream category")
        expected_maintainers = [
            {
                "email": "dev@truenas.com",
                "name": "truenas",
                "url": "https://www.truenas.com/",
            }
        ]
        if app.get("maintainers") != expected_maintainers:
            errors.append("catalog maintainers must match official generated metadata")
        if "https://apps.truenas.com/catalog/ditaknet_community/" not in app.get(
            "sources", []
        ):
            errors.append("catalog sources must include the official catalog page")
        item = _yaml(CATALOG / "item.yaml")
        if set(item) != {"categories", "icon_url", "screenshots", "tags"}:
            errors.append("catalog item.yaml shape differs from generated metadata")
        if item.get("categories") != app.get("categories"):
            errors.append("catalog item categories must match app.yaml")
        if item.get("icon_url") != app.get("icon"):
            errors.append("catalog item icon must match app.yaml")
        if item.get("screenshots") != app.get("screenshots"):
            errors.append("catalog item screenshots must match app.yaml")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, yaml.YAMLError, KeyError, TypeError) as exc:
        errors.append(f"catalog release metadata could not be validated: {exc}")
        version = ""
        lock = {}

    try:
        questions = _yaml(CATALOG / "questions.yaml")
        variables = _question_variables(questions)
        required = {"TZ", "ditaknet", "run_as", "network", "storage", "resources"}
        if set(variables) != required:
            errors.append(f"catalog question groups must be exactly {sorted(required)!r}")
        if "image_tag" in _read(CATALOG / "questions.yaml"):
            errors.append("catalog must not expose a user-controlled image tag")
        run_as = _attrs(variables.get("run_as", {}))
        for key in ("user", "group"):
            schema = run_as.get(key, {}).get("schema", {})
            if schema.get("default") != 568 or schema.get("min", 0) < 1:
                errors.append(f"catalog run_as.{key} must default to non-root ID 568")
        storage = _attrs(variables.get("storage", {}))
        if set(storage) != {"data", "logs", "backups", "plugins"}:
            errors.append("catalog must define all four persistent storage questions")
        for name, storage_question in storage.items():
            storage_attrs = _attrs(storage_question)
            host_attrs = _attrs(storage_attrs.get("host_path_config", {}))
            automatic = host_attrs.get("auto_permissions", {}).get("schema", {})
            if automatic.get("default") is not False:
                errors.append(
                    f"catalog storage.{name} must offer opt-in automatic permissions"
                )
    except (OSError, UnicodeError, ValueError, yaml.YAMLError, TypeError) as exc:
        errors.append(f"catalog questions could not be validated: {exc}")

    template = _read(CATALOG / "templates/docker-compose.yaml")
    required_template_fragments = (
        "c1.set_user(values.run_as.user, values.run_as.group)",
        "c1.set_read_only(True)",
        'c1.add_caps(["NET_RAW"])',
        '{"container_port": 5833}',
        'c1.set_network_mode("host")',
        "perm_container.add_or_skip_action",
        'c1.add_storage("/tmp"',
    )
    for fragment in required_template_fragments:
        if fragment not in template:
            errors.append(f"catalog template is missing {fragment!r}")
    if "image_tag" in template or "values.images" in template:
        errors.append("catalog template must use the catalog-controlled ix_values image tag")
    if "{% if values.network.host_network %}" not in template:
        errors.append("catalog template must render host and bridge networking exclusively")

    test_values_dir = ROOT / CATALOG / "templates/test_values"
    required_variants = {
        "basic-values.yaml",
        "host-network-values.yaml",
        "host-path-values.yaml",
        "host-path-acl-values.yaml",
    }
    present_variants = {path.name for path in test_values_dir.glob("*.yaml")}
    if not required_variants.issubset(present_variants):
        errors.append("catalog test values must cover bridge, host-network, and host-path modes")
    for name in required_variants & present_variants:
        values_doc = _yaml(CATALOG / "templates/test_values" / name)
        run_as = values_doc.get("run_as", {})
        if run_as != {"user": 568, "group": 568}:
            errors.append(f"{name}: run_as must be 568:568")
        if set(values_doc.get("storage", {})) != {"data", "logs", "backups", "plugins"}:
            errors.append(f"{name}: all four storage paths are required")
    if (test_values_dir / "host-network-values.yaml").exists():
        host_values = _yaml(CATALOG / "templates/test_values/host-network-values.yaml")
        if host_values.get("network", {}).get("host_network") is not True:
            errors.append("host-network-values.yaml must enable host networking")
    if (test_values_dir / "host-path-values.yaml").exists():
        host_path_values = _yaml(CATALOG / "templates/test_values/host-path-values.yaml")
        for name, storage_value in host_path_values.get("storage", {}).items():
            if storage_value.get("host_path_config", {}).get("auto_permissions") is not True:
                errors.append(f"host-path-values.yaml: {name} must test auto_permissions")
    if (test_values_dir / "host-path-acl-values.yaml").exists():
        acl_values = _yaml(CATALOG / "templates/test_values/host-path-acl-values.yaml")
        for name, storage_value in acl_values.get("storage", {}).items():
            config = storage_value.get("host_path_config", {})
            if config.get("acl_enable") is not True or not config.get("acl", {}).get("path"):
                errors.append(f"host-path-acl-values.yaml: {name} must test normalized ACL paths")

    placeholder_pattern = re.compile(r"REPLACE_WITH|support@example\.com")
    for relative in ("truenas-catalog", "truenas"):
        for path in (ROOT / relative).rglob("*"):
            if path.is_file() and path.suffix.lower() in {".yaml", ".yml", ".md", ".json"}:
                if placeholder_pattern.search(path.read_text(encoding="utf-8")):
                    errors.append(f"{path.relative_to(ROOT)}: unresolved catalog placeholder")

    if check_upstream and lock:
        try:
            request = urllib.request.Request(
                lock["source"], headers={"User-Agent": "DitakNet-TrueNAS-Validator/1.0"}
            )
            with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
                upstream = yaml.load(response.read().decode("utf-8"), Loader=UniqueKeyLoader)
            if upstream.get(lock["version"]) != lock["sha256"]:
                errors.append("official TrueNAS library hash differs from the local lock")
        except (OSError, UnicodeError, ValueError, KeyError, yaml.YAMLError) as exc:
            errors.append(f"official TrueNAS library hash check failed: {exc}")

    if compose_config:
        for relative in ("truenas/docker-compose.yml", "truenas/docker-compose.host-network.yml"):
            try:
                completed = subprocess.run(
                    ["docker", "compose", "-f", str(ROOT / relative), "config", "--quiet"],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if completed.returncode:
                    detail = (completed.stderr or completed.stdout).strip()
                    errors.append(f"{relative}: docker compose config failed: {detail}")
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(f"{relative}: docker compose config could not run: {exc}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check-upstream",
        action="store_true",
        help="verify the library lock against the official TrueNAS hashes file",
    )
    parser.add_argument(
        "--compose-config",
        action="store_true",
        help="run docker compose config --quiet for bridge and host variants",
    )
    args = parser.parse_args()
    errors = validate(
        check_upstream=args.check_upstream,
        compose_config=args.compose_config,
    )
    if errors:
        print("TrueNAS validation FAILED:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("TrueNAS Custom App and catalog validation OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
