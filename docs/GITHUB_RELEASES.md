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

The release workflow publishes only the exact image tag. It does not update
the GHCR `latest` alias, and an existing exact tag must never be overwritten.
A historical `:latest` alias may exist, but it is unsupported and is never
created or moved by this workflow. Record the image index and both platform
digests in release evidence; a tag is readable, while the digest identifies the
artifact bytes.

The `v2.0.1` git tag and a legacy amd64 GHCR image exist, but the corresponding
GitHub Release page is not published. The root `update-manifest.json` is a
legacy schema-v1 description of that artifact and links to the git tag tree; it
does not authorize the schema-v2 managed update handoff. Do not describe later
`main`-branch hardening, multi-architecture support, or Phase 4 update safety as
properties of that legacy artifact. A subsequent release must use a new SemVer
and report only the platforms actually built and tested.

Phase 4 code is currently unreleased. The committed channel keyring contains
empty `stable` and `beta` maps, so a release intentionally fails closed before
any registry mutation. Before the first new release, provision the channel
public keys in `ditaknet/core/update_signing_public_keys.json`, store only the
matching private keys as `UPDATE_STABLE_ED25519_PRIVATE_KEY_B64` and
`UPDATE_BETA_ED25519_PRIVATE_KEY_B64` in the `stable-release` and
`beta-release` protected GitHub environments, and protect the `update-feed`
branch. The expected initial public-key IDs are `stable-release-v1` and
`beta-release-v1`. Never commit a private signing key.

## Pre-tag checklist

1. Select a new, unused stable SemVer or an allowed `-beta.N`/`-rc.N`
   prerelease. Do not reuse `2.0.1`.
2. Update the canonical `VERSION` and every release-controlled source. Treat
   the root schema-v1 `update-manifest.json` as a legacy compatibility record,
   not the schema-v2 channel feed.
3. Finalize release notes, supported upgrade paths, compatibility warnings, and
   rollback requirements.
4. Review `release/update-policy.json`: direct-upgrade range, target database
   schema, backup format, major-version rule, and rollback policy. Only
   `state_restore_required` or `unsupported` is valid; `image_only` must be
   rejected, and unsupported must block managed preflight.
5. Verify the intended channel public key is committed and matches the private
   key in its protected environment. Protect required reviewers/secrets and the
   update-feed branch before tagging.
6. Run the release consistency, TrueNAS validation, pinned official catalog
   renderer, and full test suite.
7. Verify a fresh format-v2 backup can be created and validated, and document
   restore compatibility for the release. Exercise the stopped-container
   offline restore CLI, mounted database-directory lifetime lock, explicit stop
   for legacy/pre-lock images, checkpoint/fsync/single-replace crash boundary,
   and confirm the web/setup paths cannot replace a live database.
8. Confirm the registry either has no target exact tag or, for an authorized
   repair run, resolves to the exact digest produced by the same source. A
   different existing digest is a hard failure.

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
- the annotated tag points to a commit in `main` history;
- both platform images were independently built, smoke-tested, scanned, and
  assembled into the expected index;
- the package is public when public installation is intended;
- the exact SemVer tag resolves to the verified index digest and was finalized
  only after staging checks and OCI attestation verification;
- signed provenance and per-platform SPDX SBOM attestations verify by digest;
- the channel-scoped Ed25519 schema-v2 `update-manifest.json` verifies, binds
  the index plus `linux/amd64`/`linux/arm64` child digests, and is attached to
  the GitHub Release without overwriting a different existing asset;
- the GitHub Release page exists and names the same version, channel,
  compatibility contract, image, and digest;
- only after all preceding evidence exists, the matching `stable.json` or
  `beta.json` on the `update-feed` branch is promoted and its monotonic sequence
  is greater than the previous channel entry;
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
- for `state_restore_required`, the exact failed/new-image maintenance order:
  stop the service, run the generated SHA-256-bound offline restore command,
  preserve its pre-offline snapshot/external receipt, then select and start the
  previous exact image;
- security fixes without exposing exploit details prematurely.

## Failed or partial publication

The annotated git tag is the release trigger and immutable source identity. If
the workflow fails before the exact GHCR tag is finalized, fix the cause and
rerun against the same reviewed tag. Staging references are not customer
releases.

If the exact image tag already exists and its digest exactly matches the
reconstructed, tested staging index, the workflow may resume only the missing
metadata steps: attestations, signed manifest, GitHub Release asset, and channel
promotion. If the digest differs, the workflow refuses repair and must not
overwrite the tag. Channel promotion is last, so a partial publication cannot
advertise metadata whose image/Release evidence is incomplete.

If published bytes are wrong, revoke/deprecate that version and issue a new
SemVer. Never silently replace an immutable customer release.
