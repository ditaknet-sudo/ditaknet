# DitakNet Monitoring Server

DitakNet currently includes the complete Professional edition for every
self-hosted Docker or TrueNAS installation. No activation code, trial period,
payment flow, or external licensing server is required.

This folder contains the DitakNet monitoring server setup.

## Run

```bash
docker compose up -d
```

Open:

```text
http://localhost:5833
```

Health check:

```text
http://localhost:5833/health
```

## Important folders

- `ditaknet/` - monitoring server source code.
- `config/runtime.env` - non-secret Docker runtime defaults.
- `data/` - persistent SQLite database, generated session signing key, and runtime data.
- `logs/` - persistent server logs.
- `backups/` - persistent backups.
- `plugins/` - installed plugins.
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
- Publish a tagged image (`v2.0.1`) via `.github/workflows/publish-ghcr.yml` to
  `ghcr.io/ditaknet-sudo/ditaknet:2.0.1` (and `:latest` for testing only).
- Publish `update-manifest.json` and set `APP_UPDATE_CHECK_URL` so admins see “Update available”.
- Customers apply updates with Docker pull / TrueNAS Update (manual; backup first).

TrueNAS:

- Quick YAML install: [`docs/TRUENAS-INSTALL.md`](docs/TRUENAS-INSTALL.md)
- Compose templates: [`truenas/`](truenas/)
- Version pinning: [`docs/RELEASES.md`](docs/RELEASES.md)
- Catalog submission pack: [`truenas-catalog/`](truenas-catalog/)
- Extra notes: [`docs/TRUENAS.md`](docs/TRUENAS.md)

## Logs

- Server file logs are stored in `logs/` (container path: `/app/logs/ditaknet.log`).
- Structured activity events are available in the **System Logs** page (`/system/logs`, admin only).
- Docker Compose mounts `./logs:/app/logs`.
- TrueNAS: mount `/mnt/POOL/apps/ditaknet/logs:/app/logs`.
- Do not share exported logs publicly without reviewing for sensitive data; CSV/JSON export redacts secrets automatically.
