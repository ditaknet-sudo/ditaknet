# Changelog

All notable changes to the DitakNet monitoring server are documented in this
file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- GitHub Actions publish workflow for `ghcr.io/ditaknet-sudo/ditaknet`
  (`v*` tags → version + `latest`)
- TrueNAS SCALE Custom App compose templates under `truenas/`
- TrueNAS official catalog contribution pack under `truenas-catalog/`
- `SECURITY.md`, expanded `.env.example`, release/install docs

### Changed

- Hardened `.gitignore` / `.dockerignore` for runtime data and secrets

## [2.0.0] - 2026-07

### Added

- DitakNet monitoring server 2.0.0 packaging for Docker and TrueNAS
- Health endpoint at `/health`
- Persistent mounts for `data`, `logs`, `backups`, and `plugins`
