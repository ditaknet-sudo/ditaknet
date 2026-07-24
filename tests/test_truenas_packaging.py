"""Static regression tests for TrueNAS Compose and catalog packaging."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _load_validator():
    path = ROOT / "scripts/validate_truenas.py"
    spec = importlib.util.spec_from_file_location("validate_truenas", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _yaml(relative: str) -> dict:
    value = yaml.safe_load((ROOT / relative).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_offline_truenas_gate_passes() -> None:
    validator = _load_validator()
    assert validator.validate() == []


def test_truenas_validator_accepts_release_workflow_prerelease_versions() -> None:
    validator = _load_validator()

    for version in ("2.0.2", "2.0.3-beta.1", "2.0.3-rc.1"):
        assert validator.SOURCE_VERSION.fullmatch(version)
    for invalid in ("2.0", "2.0.3-alpha.1", "v2.0.3", "2.0.3-beta"):
        assert validator.SOURCE_VERSION.fullmatch(invalid) is None


def test_custom_app_bridge_and_host_are_mutually_exclusive() -> None:
    bridge = _yaml("truenas/docker-compose.yml")["services"]["ditaknet"]
    host = _yaml("truenas/docker-compose.host-network.yml")["services"]["ditaknet"]

    assert "network_mode" not in bridge
    assert bridge["ports"] == [
        "${DITAKNET_BIND_ADDRESS:-0.0.0.0}:${DITAKNET_PORT:-5833}:5833/tcp"
    ]
    assert host["network_mode"] == "host"
    assert "ports" not in host

    for service in (bridge, host):
        assert service["user"] == "568:568"
        assert service["read_only"] is True
        assert service["privileged"] is False
        assert service["cap_drop"] == ["ALL"]
        assert service["cap_add"] == ["NET_RAW"]
        assert service["security_opt"] == ["no-new-privileges=true"]
        assert service["pull_policy"] == "missing"
        assert {mount["target"] for mount in service["volumes"]} == {
            "/app/data",
            "/app/logs",
            "/app/backups",
            "/app/plugins",
        }
        assert all(
            mount["bind"]["create_host_path"] is False for mount in service["volumes"]
        )


def test_catalog_uses_locked_official_library_and_release_image() -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    lock = json.loads(
        (ROOT / "truenas-catalog/upstream-library.json").read_text(encoding="utf-8")
    )
    app = _yaml("truenas-catalog/ix-dev/community/ditaknet/app.yaml")
    values = _yaml("truenas-catalog/ix-dev/community/ditaknet/ix_values.yaml")

    assert app["app_version"] == version
    assert app["lib_version"] == lock["version"]
    assert app["lib_version_hash"] == lock["sha256"]
    assert len(app["lib_version_hash"]) == 64
    assert values["images"]["image"] == {
        "repository": "ghcr.io/ditaknet-sudo/ditaknet",
        "tag": version,
    }
    assert app["run_as_context"][0]["uid"] == 568
    assert app["run_as_context"][0]["gid"] == 568
    assert app["categories"] == ["monitoring"]
    assert app["maintainers"] == [
        {
            "email": "dev@truenas.com",
            "name": "truenas",
            "url": "https://www.truenas.com/",
        }
    ]
    assert "https://apps.truenas.com/catalog/ditaknet_community/" in app["sources"]

    item = _yaml("truenas-catalog/ix-dev/community/ditaknet/item.yaml")
    assert set(item) == {"categories", "icon_url", "screenshots", "tags"}
    assert item["categories"] == app["categories"]
    assert item["icon_url"] == app["icon"]
    assert item["screenshots"] == app["screenshots"]


def test_catalog_image_tag_is_not_user_controlled() -> None:
    questions = (
        ROOT / "truenas-catalog/ix-dev/community/ditaknet/questions.yaml"
    ).read_text(encoding="utf-8")
    template = (
        ROOT / "truenas-catalog/ix-dev/community/ditaknet/templates/docker-compose.yaml"
    ).read_text(encoding="utf-8")

    assert "image_tag" not in questions
    assert "image_tag" not in template
    assert "values.images" not in template
    assert '{"container_port": 5833}' in template
    assert 'c1.set_network_mode("host")' in template


def test_catalog_values_cover_network_storage_and_permission_migration() -> None:
    directory = ROOT / "truenas-catalog/ix-dev/community/ditaknet/templates/test_values"
    basic = _yaml(str((directory / "basic-values.yaml").relative_to(ROOT)))
    host = _yaml(str((directory / "host-network-values.yaml").relative_to(ROOT)))
    host_path = _yaml(str((directory / "host-path-values.yaml").relative_to(ROOT)))
    host_path_acl = _yaml(
        str((directory / "host-path-acl-values.yaml").relative_to(ROOT))
    )

    assert basic["network"]["host_network"] is False
    assert host["network"]["host_network"] is True
    assert host_path["network"]["web_port"]["port_number"] == 15833
    for values in (basic, host, host_path, host_path_acl):
        assert values["run_as"] == {"user": 568, "group": 568}
        assert set(values["storage"]) == {"data", "logs", "backups", "plugins"}
    assert all(
        storage["host_path_config"]["auto_permissions"] is True
        for storage in host_path["storage"].values()
    )
    assert all(
        storage["host_path_config"]["acl_enable"] is True
        and storage["host_path_config"]["acl"]["path"]
        for storage in host_path_acl["storage"].values()
    )


def test_migration_and_rollback_docs_have_required_safety_steps() -> None:
    install = (ROOT / "docs/TRUENAS-INSTALL.md").read_text(encoding="utf-8")
    upgrade = (ROOT / "docs/UPGRADE.md").read_text(encoding="utf-8")
    safety = (ROOT / "docs/UPDATE_AND_MIGRATION_SAFETY.md").read_text(encoding="utf-8")

    assert "568:568" in install
    assert "Create an application backup and a recursive snapshot" in install
    assert "Do not use `chmod 777`" in install
    assert "/health/deep" in upgrade
    assert 'payload["version"] == os.environ["EXPECTED_VERSION"]' in upgrade
    assert "Stop every container" in safety
    assert "`image_only`" in safety
    assert "only accepted policy values are `state_restore_required` and" in safety
    assert "offline_restore" in safety
    assert "wal_checkpoint(TRUNCATE)" in safety
    assert "Offline recovery" in safety
