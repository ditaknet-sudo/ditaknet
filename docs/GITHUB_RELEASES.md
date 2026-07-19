# GitHub release operations

This document separates a git tag, a GitHub Release record, and a GHCR image.
They are related evidence, but none proves that the other two exist.

## Release identity

For version `X.Y.Z`:

```text
Git tag:       vX.Y.Z
GitHub Release: https://github.com/ditaknet-sudo/ditaknet/releases/tag/vX.Y.Z
GHCR image:    ghcr.io/ditaknet-sudo/ditaknet:X.Y.Z
```

The stable workflow publishes only the exact image tag. It does not update
`latest`, and an existing exact tag must never be overwritten. Record the image
digest in release evidence; a tag is readable, while the digest identifies the
artifact bytes.

The `v2.0.1` git tag and a legacy amd64 GHCR image exist, but the corresponding
GitHub Release page is not published. Therefore the current manifest's
`release_url` is unresolved. Do not describe later `main`-branch hardening or
multi-architecture support as properties of that legacy artifact. A subsequent
release must use a new SemVer and report only the platforms actually built and
tested.

## Pre-tag checklist

1. Select a new, unused stable SemVer.
2. Update every version-controlled source and `update-manifest.json`.
3. Finalize release notes, supported upgrade paths, compatibility warnings, and
   rollback requirements.
4. Run the release consistency, TrueNAS validation, pinned official catalog
   renderer, and full test suite.
5. Verify a fresh backup can be created and validated, and document the backup
   format compatibility for the release.
6. Build and smoke-test the exact image that will be published.
7. Confirm the registry does not already contain the target exact tag.

Recommended local, non-deployment checks:

```bash
python scripts/ci_validate_release.py --expected X.Y.Z
python scripts/validate_truenas.py --check-upstream --compose-config
python scripts/render_truenas_catalog.py
pytest -q
```

## Publish and verify

After reviewed changes are committed:

```bash
git tag -a vX.Y.Z -m "DitakNet vX.Y.Z"
git push origin vX.Y.Z
```

Wait for the release workflow. Verify:

- all quality and security gates passed;
- the same smoke-tested artifact was pushed under `X.Y.Z`;
- the package is public when public installation is intended;
- the digest and actual architecture list are recorded;
- expected SBOM/provenance assets or attestations are present;
- the GitHub Release page exists and links to the correct changelog;
- `update-manifest.json` links resolve and name the same version/image;
- a clean host can pull the exact image and pass `/health` and `/health/deep`.

Do not update TrueNAS catalog defaults or production recommendations until all
release evidence above is available.

## Release notes minimum content

Release notes should include:

- exact image reference and digest;
- supported CPU architectures actually present in the manifest;
- database/schema or persistent-state changes;
- minimum supported source version and unsupported upgrade paths;
- backup format changes and restore compatibility;
- required UID/GID, dataset ACL, port, capability, or network changes;
- known issues and a compatibility-aware rollback procedure;
- security fixes without exposing exploit details prematurely.

## Failed or partial publication

If a workflow fails before pushing, fix the cause and rerun after confirming the
tag is still absent. If the exact image was pushed but later release metadata
failed, do not overwrite the image. Preserve its digest, repair only the missing
GitHub Release/metadata evidence, and verify all records match.

If published bytes are wrong, revoke/deprecate that version and issue a new
SemVer. Never silently replace an immutable customer release.
