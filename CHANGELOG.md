# Changelog

All notable changes to the DitakNet monitoring server are documented in this
file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Changed fresh repository Compose installs from checkout-relative bind mounts
  to Docker-managed named volumes. Existing deployments must preserve all four
  legacy bind sources through `DITAKNET_*_SOURCE` before using the new Compose
  file; the upgrade guide includes a mandatory mount/ownership preflight.

### Fixed

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
