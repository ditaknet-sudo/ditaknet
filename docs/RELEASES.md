# DitakNet versioned releases

## Tag mapping

| GitHub tag | GHCR image | Status |
| --- | --- | --- |
| `v2.0.1` | `ghcr.io/ditaknet-sudo/ditaknet:2.0.1` | Git tag and legacy amd64 image exist; GitHub Release page is not published |
| `v2.0.0` | `ghcr.io/ditaknet-sudo/ditaknet:2.0.0` | Previous legacy image |
| none | `ghcr.io/ditaknet-sudo/ditaknet:latest` | Not published or moved by the stable workflow |

Git tags include `v`; image tags do not. Stable deployments always pin a full
`X.Y.Z` tag and should record the resolved image digest. Never overwrite or
retag a published version.

Source changes made after a release tag are unreleased until a new SemVer is
created and the release workflow succeeds. In particular, do not describe the
existing `2.0.1` artifact as containing later container hardening, platform
support, or migration behavior merely because those changes exist on `main`.
Verify the release notes, image metadata, and digest of the selected artifact.

The Phase 3 Compose definition also changes the fresh-install default from
checkout-relative bind paths to named volumes. A legacy repository deployment
must explicitly retain its existing four paths through `DITAKNET_*_SOURCE`
before the new Compose file is started; see [`UPGRADE.md`](UPGRADE.md). This
mount migration warning must be included in the next release notes.

The current `update-manifest.json` points to a `v2.0.1` GitHub Release URL that
does not yet resolve because only the git tag exists. Publishing or correcting
that release record is tracked as release-process debt; clients must treat a
missing release page as unavailable evidence, not as proof of a completed
release.

## Release gate

A release candidate must pass, in order:

1. locked dependency installation and vulnerability/security checks;
2. complete automated tests and release/version consistency checks;
3. Docker image build and `/health` smoke test of that exact image;
4. TrueNAS bridge/host Compose validation and catalog static/render tests;
5. immutable-tag guard confirming the version does not already exist;
6. publication of the already smoke-tested artifact under the exact tag;
7. release evidence such as SBOM/provenance when configured by the workflow.

The release page must state the supported platforms actually present in the
published image manifest. Do not claim multi-architecture support unless those
architectures were built and tested.

## Version synchronization

Before tagging a new release, update all release-controlled sources together,
including:

- application/configuration version defaults;
- Dockerfile and root/TrueNAS Compose exact fallbacks;
- `.env.example` files;
- `update-manifest.json` and release notes;
- TrueNAS `app.yaml` `app_version` and `ix_values.yaml` image tag;
- TrueNAS test values and user documentation.

Then run:

```bash
python scripts/ci_validate_release.py --expected X.Y.Z
python scripts/validate_truenas.py --check-upstream --compose-config
pytest -q
```

The catalog image tag is controlled by `ix_values.yaml`. It is not a user input;
catalog upgrades move it only through a reviewed catalog version.

## Publish

After all source changes are committed and reviewed:

```bash
git tag -a vX.Y.Z -m "DitakNet vX.Y.Z"
git push origin vX.Y.Z
```

Confirm the GitHub Actions release run, GHCR package visibility, exact tag,
digest, image architecture list, health smoke evidence, and release assets.
Only then update production or TrueNAS instructions to make the new version the
recommended tag.

## TrueNAS release consumption

TrueNAS Custom Apps and catalog installs keep four persistent storage mounts
while changing only the exact image version. The Compose definitions use
`pull_policy: missing`, so an offline restart can use the already cached exact
artifact; an upgrade explicitly selects/pulls another tag. Always follow the
backup, snapshot, health validation, and compatibility-aware rollback process
in [`UPGRADE.md`](UPGRADE.md).
