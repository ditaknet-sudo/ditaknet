"""Static release-container security and multi-architecture invariants."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_dockerfile_uses_digest_pinned_base_and_non_root_runtime() -> None:
    dockerfile = _read("Dockerfile")

    assert re.search(
        r"^FROM python:3\.11-slim@sha256:[0-9a-f]{64}$",
        dockerfile,
        re.MULTILINE,
    )
    assert re.search(r"^ARG APP_UID=568$", dockerfile, re.MULTILINE)
    assert re.search(r"^ARG APP_GID=568$", dockerfile, re.MULTILINE)
    assert re.search(r"^USER \$\{APP_UID}:\$\{APP_GID}$", dockerfile, re.MULTILINE)
    assert "data.get('status') == 'healthy'" in dockerfile
    assert "pip uninstall --yes setuptools wheel" in dockerfile
    assert "pip uninstall --yes pip" in dockerfile


def test_web_process_owns_data_lock_before_opening_sqlite() -> None:
    main = _read("ditaknet/main.py")

    acquire = main.index("runtime_lock = acquire_runtime_lock(settings.db_path.parent)")
    open_database = main.index("await db.init_db(str(settings.db_path))")
    close_database = main.index("await db.close_db()")
    release = main.index("runtime_lock.release()")

    assert acquire < open_database
    assert close_database < release


def test_root_compose_applies_minimal_runtime_privileges() -> None:
    compose = _read("docker-compose.yml")

    assert re.search(
        r"image:\s*ghcr\.io/ditaknet-sudo/ditaknet:\$\{DITAKNET_VERSION:-\d+\.\d+\.\d+\}",
        compose,
    )
    assert "build:" not in compose
    assert re.search(r"^\s+pull_policy:\s*missing\s*$", compose, re.MULTILINE)
    assert compose.count("${DITAKNET_VERSION:-2.0.1}") == 4
    assert "${DITAKNET_BIND_ADDRESS:-0.0.0.0}:${DITAKNET_PORT:-5833}:5833" in compose
    for name, volume in (
        ("DATA", "ditaknet-data"),
        ("LOGS", "ditaknet-logs"),
        ("BACKUPS", "ditaknet-backups"),
        ("PLUGINS", "ditaknet-plugins"),
    ):
        assert f"${{DITAKNET_{name}_SOURCE:-{volume}}}:/app/" in compose
        assert re.search(rf"^  {volume}:\s*$", compose, re.MULTILINE)
    assert "8.8.8.8" not in compose
    assert "storage-init:" in compose
    assert re.search(
        r"storage-init:.*?user:\s*[\"']0:0[\"'].*?cap_drop:\s*\n\s+- ALL"
        r".*?cap_add:\s*\n\s+- CHOWN.*?command:\s*\n\s+- chown"
        r"\s*\n\s+- --recursive\s*\n\s+- --no-dereference"
        r"\s*\n\s+- [\"']568:568[\"']",
        compose,
        re.DOTALL,
    )
    init_block, app_block = compose.split("  ditaknet:", 1)
    assert "${DITAKNET_DATA_SOURCE" not in init_block
    assert "condition: service_completed_successfully" in app_block
    assert re.search(r'^\s+user:\s*["\']568:568["\']\s*$', compose, re.MULTILINE)
    assert re.search(r"^\s+read_only:\s*true\s*$", compose, re.MULTILINE)
    assert re.search(r"^\s+pids_limit:\s*256\s*$", compose, re.MULTILINE)
    assert "no-new-privileges:true" in compose
    assert re.search(r"^\s+cap_drop:\s*\n\s+- ALL\s*$", compose, re.MULTILINE)
    assert re.search(r"^\s+cap_add:\s*\n\s+- NET_RAW\s*$", compose, re.MULTILINE)
    assert "cap_add:" in compose and compose.count("- NET_RAW") == 1
    assert "/tmp:rw,noexec,nosuid,nodev" in compose
    assert "data.get('status') == 'healthy'" in compose


def test_release_workflow_preserves_and_gates_both_architecture_artifacts() -> None:
    workflow = _read(".github/workflows/publish-ghcr.yml")

    for platform in ("linux/amd64", "linux/arm64"):
        assert f"platforms: {platform}" in workflow
    assert '--platform "linux/${arch}"' in workflow
    assert "smoke_image amd64" in workflow
    assert "smoke_image arm64" in workflow
    assert workflow.index(
        "Set up QEMU for arm64 runtime smoke testing"
    ) < workflow.index("Set up Docker Buildx")

    for arch in ("amd64", "arm64"):
        assert f"ditaknet:ci-{arch}" in workflow
        assert f"ditaknet-image-{arch}.tar.gz" in workflow
        assert f"ditaknet-image-{arch}.spdx.json" in workflow
        assert f"image-ref: ditaknet:ci-{arch}" in workflow

    assert workflow.count("aquasecurity/trivy-action@") == 4
    assert workflow.count('exit-code: "1"') == 2
    assert workflow.count('exit-code: "0"') == 2
    assert workflow.count("ignore-unfixed: true") == 2
    assert workflow.count("provenance: false") == 2
    assert workflow.count("sbom: false") == 2
    assert workflow.count("name: ditaknet-image-${{ github.run_id }}") == 2
    assert (
        "name: ditaknet-image-${{ github.run_id }}-${{ github.run_attempt }}"
        not in workflow
    )
    assert "overwrite: true" in workflow
    assert "docker buildx imagetools create" in workflow
    assert "${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-$arch" in workflow
    assert "${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-index" in workflow
    assert workflow.index("Finalize the immutable SemVer release tag") > workflow.index(
        "Attest the smoke-tested arm64 image SBOM"
    )
    assert (
        "PUBLISHED_DIGEST" in workflow
        and '"$PUBLISHED_DIGEST" != "$INDEX_DIGEST"' in workflow
    )
    assert '{"linux/amd64", "linux/arm64"}' in workflow
    assert "--read-only" in workflow
    assert "--cap-drop ALL" in workflow
    assert "--cap-add NET_RAW" in workflow
    assert 'removeprefix("CAP_")' in workflow
    assert 'host["Privileged"] is False' in workflow
    assert "--security-opt no-new-privileges:true" in workflow
    assert "/health/deep" in workflow
    assert 'payload["overall_status"] == "pass"' in workflow
    assert 'payload["build"]["image_tag"] == expected_version' in workflow
    assert "docker volume create" in workflow
    assert "legacy/root-owned.txt" in workflow
    assert "--entrypoint chown" in workflow
    assert "--recursive --no-dereference 568:568" in workflow
    assert "docker restart" in workflow
    assert "ci_persistence_marker" in workflow
    assert "ditaknet:ci-local" not in workflow
    assert ":latest" not in workflow
    assert "refs/tags/v$VERSION:refs/tags/v$VERSION" in workflow
    assert '"$TAG_COMMIT" != "$SOURCE_COMMIT"' in workflow


def test_workflow_actions_are_immutable_sha_pinned() -> None:
    workflow = _read(".github/workflows/publish-ghcr.yml")
    uses = re.findall(r"^\s*uses:\s*([^\s#]+)", workflow, re.MULTILINE)

    assert uses
    for action in uses:
        assert re.search(r"@[0-9a-f]{40}$", action), action


def test_dependabot_tracks_all_release_dependency_ecosystems() -> None:
    config = _read(".github/dependabot.yml")

    ecosystems = set(re.findall(r"package-ecosystem:\s*([a-z-]+)", config))
    assert ecosystems == {"pip", "docker", "github-actions"}


def test_compose_overrides_are_discoverable_without_secrets() -> None:
    example = _read(".env.example")

    for key in (
        "DITAKNET_VERSION",
        "DITAKNET_BIND_ADDRESS",
        "DITAKNET_PORT",
        "DITAKNET_DATA_SOURCE",
        "DITAKNET_LOGS_SOURCE",
        "DITAKNET_BACKUPS_SOURCE",
        "DITAKNET_PLUGINS_SOURCE",
    ):
        assert re.search(rf"^# {key}=", example, re.MULTILINE), key
    assert "568:568" in example


def test_legacy_bind_mount_upgrade_cannot_be_silently_skipped_in_docs() -> None:
    readme = _read("README.md")
    upgrade = _read("docs/UPGRADE.md")

    assert "Existing checkout warning" in readme
    assert "Legacy repository Compose migration" in upgrade
    for key in (
        "DITAKNET_DATA_SOURCE",
        "DITAKNET_LOGS_SOURCE",
        "DITAKNET_BACKUPS_SOURCE",
        "DITAKNET_PLUGINS_SOURCE",
    ):
        assert key in upgrade
    assert "docker compose up -d --no-deps ditaknet" not in upgrade
    assert "docker compose config" in upgrade
