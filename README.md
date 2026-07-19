# DitakNet Monitoring Server

DitakNet currently includes the complete Professional edition for every
self-hosted Docker or TrueNAS installation. No activation code, trial period,
payment flow, or external licensing server is required.

This folder contains the DitakNet monitoring server setup.

## Run

For a **new installation**, the default Compose deployment uses four
Docker-managed named volumes and runs
the application as non-root UID/GID `568:568`. The narrowly scoped
`storage-init` service grants that identity access only to those named volumes.

> **Existing checkout warning:** older DitakNet Compose files mounted
> `./data`, `./logs`, `./backups`, and `./plugins`. Do not start this newer
> Compose file until all four existing paths are preserved through the
> `DITAKNET_*_SOURCE` variables and prepared for UID/GID `568:568`; otherwise
> Docker will attach new empty named volumes. Follow the
> [legacy repository Compose migration](docs/UPGRADE.md#legacy-repository-compose-migration).

```bash
docker compose up -d
```

Open:

```text
http://localhost:5833
```

Health checks:

```text
http://localhost:5833/health
http://localhost:5833/health/deep
```

To use bind mounts instead, set `DITAKNET_DATA_SOURCE`,
`DITAKNET_LOGS_SOURCE`, `DITAKNET_BACKUPS_SOURCE`, and
`DITAKNET_PLUGINS_SOURCE` to provisioned host paths. Before the first start,
grant UID/GID `568:568` read/write/execute access to each path. The init service
intentionally never receives operator-supplied bind paths.

## Important folders

- `ditaknet/` - monitoring server source code.
- `config/runtime.env` - non-secret Docker runtime defaults.
- `ditaknet-data` - default named volume for the SQLite database, generated
  session signing key, and runtime data.
- `ditaknet-logs` - default named volume for persistent server logs.
- `ditaknet-backups` - default named volume for backups.
- `ditaknet-plugins` - default named volume for installed plugins.
- `docker-compose.yml` - Docker/TrueNAS-style runtime setup.

Do not store passwords, tokens, or private keys in repository files. User
passwords are stored only as hashes in the SQLite database. When no external
session secret is supplied, DitakNet generates a persistent signing key under
`data/`.

Do not commit `data/`, `logs/`, `backups/`, local `*.env` files, or runtime
exports.

## Version and updates

Current app version: **2.0.1**.

- Pushing code to GitHub does **not** auto-update running customer servers.
- The existing `ghcr.io/ditaknet-sudo/ditaknet:2.0.1` image is a legacy,
  single-architecture `linux/amd64` artifact. It predates the Phase 3 non-root,
  multi-architecture build pipeline and must not be overwritten.
- Phase 3 prepares the next SemVer release for both `linux/amd64` and
  `linux/arm64`. Publishing requires a new matching Git tag and an explicit
  manual release workflow; no floating `:latest` tag is used.
- Publish `update-manifest.json` and set `APP_UPDATE_CHECK_URL` so admins see “Update available”.
- Customers apply updates with Docker pull / TrueNAS Update (manual; backup first).
- Follow [`docs/UPGRADE.md`](docs/UPGRADE.md) and
  [`docs/UPDATE_AND_MIGRATION_SAFETY.md`](docs/UPDATE_AND_MIGRATION_SAFETY.md)
  before changing an installed version.

TrueNAS:

- Quick YAML install: [`docs/TRUENAS-INSTALL.md`](docs/TRUENAS-INSTALL.md)
- Compose templates: [`truenas/`](truenas/)
- Version pinning: [`docs/RELEASES.md`](docs/RELEASES.md)
- Catalog submission pack: [`truenas-catalog/`](truenas-catalog/)
- Extra notes: [`docs/TRUENAS.md`](docs/TRUENAS.md)

## Logs

- Server file logs are stored in the `ditaknet-logs` named volume by default
  (container path: `/app/logs/ditaknet.log`).
- Structured activity events are available in the **System Logs** page (`/system/logs`, admin only).
- A configured `DITAKNET_LOGS_SOURCE` bind path replaces the default named volume.
- TrueNAS: mount `/mnt/POOL/apps/ditaknet/logs:/app/logs`.
- Do not share exported logs publicly without reviewing for sensitive data; CSV/JSON export redacts secrets automatically.

## Dependency locks

Release installs use complete SHA-256 hash-locked dependency graphs:

- `requirements-app-direct.txt` — reviewed direct runtime inputs.
- `requirements-ci-direct.txt` — reviewed direct CI/test inputs.
- `requirements.txt` — generated runtime lock used by Docker.
- `requirements-ci.txt` — generated combined runtime and CI lock.

Use exactly `uv 0.11.29`, then run `python scripts/lock_dependencies.py` to
refresh locks intentionally or `python scripts/lock_dependencies.py --check`
to verify them without changing files. Docker and CI both install with
`--require-hashes`.
