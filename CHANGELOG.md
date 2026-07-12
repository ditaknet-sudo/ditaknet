# Changelog

All notable changes to the DitakNet monitoring server are documented in this
file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

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
