# Changelog

All notable changes to the DitakNet monitoring server are documented in this
file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Made `VERSION` the canonical source version while keeping the root
  `update-manifest.json` as a schema-v1 legacy record for the published `2.0.1`
  amd64 artifact.
- Upgraded backup archives to format 2 with per-member SHA-256 checks, final
  archive validation, SQLite integrity/foreign-key checks, and operation context
  for pre-update and pre-migration recovery points.
- Replaced in-process and setup-time database restore with an offline-only,
  one-shot maintenance CLI. Settings now validates the recovery point and shows
  the command but never replaces the database while the web process is alive.
- Changed fresh repository Compose installs from checkout-relative bind mounts
  to Docker-managed named volumes. Existing deployments must preserve all four
  legacy bind sources through `DITAKNET_*_SOURCE` before using the new Compose
  file; the upgrade guide includes a mandatory mount/ownership preflight.

### Fixed

- Made official update verification fail closed by default across network,
  signature, channel, cache-policy, digest, and replay failures, without an
  unsigned GitHub Releases fallback.
- Prevented future-schema databases and unsafe application downgrades from
  mutating persistent state by enforcing schema, minimum-reader, last-writer
  SemVer, and migration-fingerprint guards before migration.
- Rejected `image_only` from signed schema-v2 compatibility and managed
  preflight; rollback policy is limited to `state_restore_required` or
  `unsupported` because writer-version guards make tag-only rollback unsafe.
- Prevented restore races with requests, schedulers, or plugins by holding an
  exclusive mounted database-directory lock for the complete web-process
  lifetime and requiring the offline CLI to acquire the same lock before
  replacement. Legacy/pre-lock images still require an explicit operator stop.
- Bounded backup uploads and ZIP validation by compressed/uncompressed size,
  member count and per-member size, compression ratio, safe unique paths, and
  streaming checksums; blocking web validation now runs on a worker thread.
- Removed unsafe dynamic HTML from the updates UI and restricted release links
  to expected HTTPS destinations.
- Made fresh Docker installs writable as non-root by default through
  Docker-managed volumes and a capability-scoped ownership initializer that
  never receives operator bind paths.
- Added complete TrueNAS host-path permission choices, including explicit
  automatic ownership and ACL-managed modes for all four persistent datasets.
- Made immutable image publication transactional by keeping the public SemVer
  tag absent until the multi-architecture index and attestations are complete.
- Made SQLite backups WAL-safe by using an online snapshot and validating ZIP,
  manifest, and database integrity before restore.
- Made additive migration bookkeeping idempotent on fresh and existing databases.
- Fixed SemVer prerelease comparisons containing mixed numeric and text identifiers.
- Fixed embedded update-manifest HMAC verification by signing canonical JSON
  without the self-referential `signature` field.
- Removed the test-environment CSRF bypass and enabled CSRF protection for HR,
  employee-presence, first-run setup form posts, and session-authenticated API
  mutations while preserving bearer-token API clients.
- Prevented a configured signed-update channel from falling back to unsigned
  GitHub release metadata after a manifest or signature failure.
- Escaped unexpected status labels before rendering badge markup.
- Corrected TCP refused-connection retry behavior and invalid scheduler retry fallback.
- Removed case-ambiguous translation keys and aligned Armenian, English, and Russian
  locale key sets.
- Forced production mode by default in Python, Docker, both TrueNAS Compose
  variants, and the TrueNAS catalog template.
- Removed the Starlette TestClient deprecation warning by using `httpx2` only
  in the isolated CI/test dependency graph.

### Added

- Added strict schema-v2 `stable` and `beta` update manifests with
  channel-scoped Ed25519 key rotation, exact GHCR index and
  `linux/amd64`/`linux/arm64` digest binding, signed compatibility contracts,
  and monotonic per-channel replay protection.
- Added an admin-only, exact `UPDATE X.Y.Z` preflight that force-refreshes
  trusted metadata, creates and validates a target-bound recovery backup, and
  returns a revalidated two-hour receipt containing external Docker/TrueNAS and
  rollback instructions. DitakNet never performs the redeploy itself.
- Added stopped-container restore with exact `RESTORE <filename>` and approved
  SHA-256 confirmation, a validated/fsynced pre-offline database snapshot, and
  an external JSON receipt. The stopped current DB is checkpointed with WAL
  `TRUNCATE`, sidecars are cleared, current/staged files are validated and
  fsynced, and one final `os.replace` plus directory fsync provides the
  crash-atomic swap. The maintenance image does not initialize or restamp the
  restored database before the previous exact image starts.
- Added database pre-migration backups and deep-health evidence for application
  version, database schema/minimum reader, and migration fingerprint state.
- Extended the tag-triggered release workflow with protected channel signing,
  digest-bound signed metadata, verified OCI provenance/SBOM attestations,
  same-digest-only metadata repair, safe GitHub Release asset publication, and
  selected channel-feed promotion as the final step.
- Added independently built and smoke-tested `linux/amd64` and `linux/arm64`
  images, with exact platform-index validation, restart persistence probes,
  deep health/version checks, and per-architecture SPDX SBOMs.
- Added a non-root UID/GID `568:568` runtime, read-only root filesystem,
  `no-new-privileges`, PID limits, and an allowlist containing only `NET_RAW`.
- Added two-tier Trivy scanning: complete vulnerability reports plus a release
  gate for fixable high/critical findings.
- Added deterministic TrueNAS catalog validation and rendering against a pinned
  official `truenas/apps` library commit for bridge, host-network, automatic
  permission, and ACL variants.
- Added Docker/TrueNAS install, upgrade, rollback, release, and update/migration
  safety documentation, plus Dependabot coverage for pip, Docker, and Actions.
- Added isolated regression coverage for authentication/RBAC/CSRF, monitoring
  checks, scheduler retries, health aggregation, SQLite migrations, updates,
  release consistency, locale JSON, and backup/restore compatibility.
- Added a three-stage GitHub Actions quality, image-smoke, and immutable GHCR
  publish pipeline with dependency/security audits and exact smoke-tested image
  handoff between the build and publish jobs.
- Added universal SHA-256 hash-locked runtime/CI dependency graphs with pinned
  lock generation and freshness validation.
- Added SPDX SBOM generation from the smoke-tested image and signed SLSA
  provenance/SBOM attestations bound to the published GHCR digest.

## [2.0.1] - 2026-07-12

### Fixed

- Fixed unexpected server error handling on dashboard / API paths caused by
  unsafe numeric coercion of legacy check and license fields
- Improved Request ID logging so every server error writes the same
  `req_…` id into backend logs with a full stack trace
- Improved TrueNAS storage validation with real write probes and clear
  per-path errors for `/app/data`, `/app/logs`, `/app/backups`, `/app/plugins`

### Changed

- Upgraded GitHub Actions to Node 24-compatible action releases
  (`checkout@v7`, `setup-buildx@v4`, `login@v4`, `build-push@v7`)
- Added upgrade and smoke tests before GHCR publish (compile, pytest,
  compose config, container `/health`, login page)

## [2.0.0] - 2026-07

### Added

- DitakNet monitoring server 2.0.0 packaging for Docker and TrueNAS
- Health endpoint at `/health`
- Persistent mounts for `data`, `logs`, `backups`, and `plugins`
