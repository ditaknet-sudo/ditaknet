# DitakNet versioned releases

## Tag mapping

| GitHub tag | GHCR image | Status |
| --- | --- | --- |
| `v2.0.2` | `ghcr.io/ditaknet-sudo/ditaknet:2.0.2` | Release target; it becomes supported only after the tag, image, attestations, GitHub Release, and signed feed are all public and verified |
| `v2.0.1` | `ghcr.io/ditaknet-sudo/ditaknet:2.0.1` | Git tag and legacy amd64 image exist; GitHub Release page is not published |
| `v2.0.0` | `ghcr.io/ditaknet-sudo/ditaknet:2.0.0` | Previous legacy image |
| none | `ghcr.io/ditaknet-sudo/ditaknet:latest` | A historical alias may exist; unsupported and never published or moved by the current workflow |

Git tags include `v`; image tags do not. Stable deployments always pin a full
`X.Y.Z` tag and should record the resolved image digest. Never overwrite or
retag a published version.

Source changes made after a release tag are unreleased until a new SemVer is
created and the release workflow succeeds. The `2.0.1` artifact never gains
the hardening, platform support, or migration behavior shipped by `2.0.2`.
Verify the versioned release notes, image metadata, signed manifest, and digest
of the selected artifact.

The Phase 3 Compose definition also changes the fresh-install default from
checkout-relative bind paths to named volumes. A legacy repository deployment
must explicitly retain its existing four paths through `DITAKNET_*_SOURCE`
before the new Compose file is started; see [`UPGRADE.md`](UPGRADE.md). This
mount migration warning is included in
[`release/notes/2.0.2.md`](../release/notes/2.0.2.md).

The root `update-manifest.json` is a schema-v1 legacy feed for `2.0.1`. It links
to the immutable git tag tree because no GitHub Release was published, reports
only `linux/amd64`, and cannot unlock the Phase 4 managed handoff. New releases
use a strict, signed schema-v2 manifest attached to the GitHub Release and then
promoted to the selected `stable` or `beta` feed.

The stable keyring contains the public `stable-release-v1` trust anchor. Its
matching private key is stored only in the `stable-release` GitHub environment.
Beta remains fail-closed until a separate beta key is provisioned. A source
commit alone is never advertised as a release: the exact tag, image,
attestations, GitHub Release, and promoted signed feed must all be verified.

## Release gate

A release candidate must pass, in order:

1. locked dependency installation and vulnerability/security checks;
2. complete automated tests and release/version consistency checks, including
   mounted database-directory lifetime-lock enforcement, explicit legacy/
   pre-lock shutdown, live/setup restore rejection, bounded ZIP validation, and
   stopped-container restore/receipt recovery;
3. Docker image build and `/health` smoke test of that exact image;
4. TrueNAS bridge/host Compose validation and catalog static/render tests;
5. channel signing-key verification before any registry mutation;
6. independently smoke-tested `linux/amd64` and `linux/arm64` staging images,
   index/digest validation, scans, and verified OCI provenance/SBOM
   attestations;
7. signed schema-v2 manifest generation and verification against the committed
   channel keyring;
8. immutable exact-tag finalization, or source-attested existing-digest
   metadata repair without rebuilding or mutating the image;
9. GitHub Release creation/resume with the byte-identical manifest asset;
10. monotonic selected-channel feed promotion last.

Schema-v2 rollback policy is limited to `state_restore_required` or
`unsupported`. `image_only` must fail validation, and `unsupported` must block
the managed preflight rather than emit tag-only rollback instructions.

The release page must state the supported platforms actually present in the
published image manifest. Do not claim multi-architecture support unless those
architectures were built and tested.

## Version synchronization

Before tagging a new release, update all release-controlled sources together,
including:

- canonical `VERSION` and the derived application/configuration version
  defaults;
- Dockerfile and root/TrueNAS Compose exact fallbacks;
- `.env.example` files;
- the immutable per-version file under `release/notes/` and
  `release/update-policy.json`; keep the root schema-v1
  `update-manifest.json` unchanged as the historical `2.0.1` record;
- the channel public-key ring when performing an approved signing-key rotation;
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
index/platform digests, health smoke evidence, verified OCI attestations,
signed GitHub Release manifest asset, and selected channel feed. Only then
update production or TrueNAS instructions to make the new version the
recommended tag. The application only produces an external redeploy handoff;
it never changes the Docker or TrueNAS App by itself.

## TrueNAS release consumption

TrueNAS Custom Apps and catalog installs keep four persistent storage mounts
while changing only the exact image version. The Compose definitions use
`pull_policy: missing`, so an offline restart can use the already cached exact
artifact; an upgrade explicitly selects/pulls another tag. Always follow the
backup, snapshot, health validation, and compatibility-aware rollback process
in [`UPGRADE.md`](UPGRADE.md). A `state_restore_required` rollback stops the
App and every legacy/pre-lock container, recovers all recorded mounted datasets
from the recursive pre-update ZFS snapshot, or restores with the failed/new
image's one-shot maintenance CLI against the exact same Data/Backups mounts.
Only after recovery succeeds does the operator select the previous exact image,
start the App, and require passing deep health. Live restore through Settings or
setup is unsupported.
