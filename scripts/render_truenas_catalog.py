#!/usr/bin/env python3
"""Render every DitakNet catalog variant with pinned official TrueNAS code.

This is a render-only validation: it fetches the locked ``truenas/apps`` commit
into a temporary directory, imports that commit's official app library, renders
the Jinja template, and asserts the resulting Compose security/network/storage
contract. It never pulls or starts the DitakNet application image.
"""

from __future__ import annotations

import argparse
import copy
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from typing import Any

import jinja2
import yaml


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "truenas-catalog/ix-dev/community/ditaknet"
LOCK = ROOT / "truenas-catalog/upstream-library.json"
VARIANTS = {
    "basic-values.yaml": "bridge",
    "host-network-values.yaml": "host",
    "host-path-values.yaml": "host-path",
    "host-path-acl-values.yaml": "host-path-acl",
}
OFFICIAL_REPOSITORY = "https://github.com/truenas/apps.git"
COMMIT_PATTERN = "0123456789abcdef"


def _merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _run(command: list[str], *, cwd: Path) -> None:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"{' '.join(command)} failed: {detail}")


class _NoDockerNetworks:
    def list(self) -> list[Any]:
        return []


class _NoDockerClient:
    networks = _NoDockerNetworks()


def _load_official_render(library_source: Path, import_root: Path) -> Any:
    """Import the official library under a valid temporary package name."""

    package = import_root / "truenas_ix_lib"
    shutil.copytree(library_source, package)
    fake_docker = types.ModuleType("docker")
    fake_docker.from_env = lambda: _NoDockerClient()  # type: ignore[attr-defined]
    sys.modules["docker"] = fake_docker
    # bcrypt is an official-library optional render helper; DitakNet's template
    # does not call it, so a no-op import shim avoids adding an unrelated app
    # runtime dependency to this deterministic renderer.
    def _unexpected_bcrypt(*_args: Any, **_kwargs: Any) -> bytes:
        raise AssertionError("DitakNet catalog unexpectedly invoked the bcrypt helper")

    fake_bcrypt = types.ModuleType("bcrypt")
    fake_bcrypt.gensalt = _unexpected_bcrypt  # type: ignore[attr-defined]
    fake_bcrypt.hashpw = _unexpected_bcrypt  # type: ignore[attr-defined]
    sys.modules.setdefault("bcrypt", fake_bcrypt)
    # The official Linux-targeted library uses os.uname() only to detect a
    # TrueNAS host. Provide the equivalent negative result on Windows CI/dev.
    if not hasattr(os, "uname"):
        os.uname = lambda: types.SimpleNamespace(release="")  # type: ignore[attr-defined]
    sys.path.insert(0, str(package))
    sys.path.insert(0, str(import_root))
    return importlib.import_module("truenas_ix_lib.render")


def _render_variant(render_module: Any, values_file: str) -> dict[str, Any]:
    base = yaml.safe_load((APP / "ix_values.yaml").read_text(encoding="utf-8"))
    variant = yaml.safe_load(
        (APP / "templates/test_values" / values_file).read_text(encoding="utf-8")
    )
    values = _merge(base, variant)
    environment = jinja2.Environment(
        extensions=["jinja2.ext.do"],
        undefined=jinja2.StrictUndefined,
        autoescape=False,
    )
    template = environment.from_string(
        (APP / "templates/docker-compose.yaml").read_text(encoding="utf-8")
    )
    ix_lib = types.SimpleNamespace(
        base=types.SimpleNamespace(render=render_module)
    )
    rendered = template.render(values=values, ix_lib=ix_lib)
    document = json.loads(rendered)
    if not isinstance(document, dict):
        raise AssertionError(f"{values_file}: rendered output is not an object")
    return document


def _assert_contract(name: str, mode: str, document: dict[str, Any]) -> None:
    services = document.get("services", {})
    service = services.get("ditaknet")
    if not isinstance(service, dict):
        raise AssertionError(f"{name}: rendered DitakNet service is missing")

    assertions = {
        "user": "568:568",
        "read_only": True,
        "privileged": False,
        "init": True,
        "cap_drop": ["ALL"],
        "cap_add": ["NET_RAW"],
    }
    for key, expected in assertions.items():
        if service.get(key) != expected:
            raise AssertionError(
                f"{name}: {key} expected {expected!r}, got {service.get(key)!r}"
            )
    if service.get("security_opt") != ["no-new-privileges=true"]:
        raise AssertionError(f"{name}: no-new-privileges is missing")
    if service.get("environment", {}).get("APP_ENV") != "production":
        raise AssertionError(f"{name}: APP_ENV is not production")

    targets = {
        mount.get("target")
        for mount in service.get("volumes", [])
        if isinstance(mount, dict)
    }
    expected_targets = {"/app/data", "/app/logs", "/app/backups", "/app/plugins"}
    if targets != expected_targets:
        raise AssertionError(f"{name}: persistent targets are {sorted(targets)!r}")
    if not any(str(entry).startswith("/tmp:") for entry in service.get("tmpfs", [])):
        raise AssertionError(f"{name}: /tmp tmpfs is missing")

    ports = service.get("ports", [])
    portals = document.get("x-portals", [])
    if mode == "host":
        if service.get("network_mode") != "host" or ports:
            raise AssertionError(f"{name}: host mode rendered published ports")
        if portals:
            raise AssertionError(f"{name}: host mode rendered a bridge portal")
    else:
        if service.get("network_mode"):
            raise AssertionError(f"{name}: bridge mode rendered host networking")
        expected_host_port = {
            "bridge": 5833,
            "host-path": 15833,
            "host-path-acl": 25833,
        }[mode]
        if not any(
            port.get("target") == 5833
            and int(port.get("published", 0)) == expected_host_port
            for port in ports
            if isinstance(port, dict)
        ):
            raise AssertionError(
                f"{name}: expected host {expected_host_port} -> container 5833"
            )
        if not portals:
            raise AssertionError(f"{name}: bridge WebUI portal is missing")

    dependency = service.get("depends_on", {}).get("permissions", {})
    if mode == "host-path-acl":
        if "permissions" in services or dependency:
            raise AssertionError(f"{name}: ACL-managed paths rendered a chown helper")
    else:
        if "permissions" not in services:
            raise AssertionError(f"{name}: permissions helper did not render")
        if dependency.get("condition") != "service_completed_successfully":
            raise AssertionError(f"{name}: DitakNet does not wait for permissions helper")


def render_all() -> tuple[str, list[str]]:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    commit = lock["catalog_commit"]
    repository = lock["catalog_repository"]
    version = lock["version"]
    if repository != OFFICIAL_REPOSITORY:
        raise AssertionError("catalog lock does not use the official truenas/apps repository")
    if len(commit) != 40 or any(character not in COMMIT_PATTERN for character in commit):
        raise AssertionError("catalog lock commit is not a full lowercase Git SHA")
    os.environ.setdefault("FAKE_ENV", "1")

    with tempfile.TemporaryDirectory(prefix="ditaknet-truenas-render-") as temporary:
        checkout = Path(temporary) / "apps"
        checkout.mkdir()
        _run(["git", "init", "--quiet"], cwd=checkout)
        _run(["git", "remote", "add", "origin", repository], cwd=checkout)
        _run(["git", "config", "core.sparseCheckout", "true"], cwd=checkout)
        (checkout / ".git/info/sparse-checkout").write_text(
            f"/library/{version}/\n/library/hashes.yaml\n",
            encoding="utf-8",
        )
        _run(["git", "fetch", "--quiet", "--depth", "1", "origin", commit], cwd=checkout)
        _run(["git", "checkout", "--quiet", "--detach", "FETCH_HEAD"], cwd=checkout)

        hashes = yaml.safe_load((checkout / "library/hashes.yaml").read_text(encoding="utf-8"))
        if hashes.get(version) != lock["sha256"]:
            raise AssertionError("pinned official commit does not contain the locked library hash")

        render_module = _load_official_render(
            checkout / "library" / version,
            Path(temporary),
        )
        rendered: list[str] = []
        for values_file, mode in VARIANTS.items():
            document = _render_variant(render_module, values_file)
            _assert_contract(values_file, mode, document)
            rendered.append(values_file)
    return commit, rendered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    try:
        commit, variants = render_all()
    except (
        OSError,
        ValueError,
        RuntimeError,
        AssertionError,
        AttributeError,
        ImportError,
        KeyError,
        jinja2.TemplateError,
    ) as exc:
        print(f"Pinned official TrueNAS render FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"Pinned official TrueNAS render OK: {commit}")
    for variant in variants:
        print(f"  - {variant}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
