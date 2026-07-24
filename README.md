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

Current app version: **2.0.2**.

- Pushing code to GitHub does **not** auto-update running customer servers.
- The existing `ghcr.io/ditaknet-sudo/ditaknet:2.0.1` image is a legacy,
  single-architecture `linux/amd64` artifact. It predates the Phase 3/4
  hardening and signed-update work and must not be overwritten. The root
  `update-manifest.json` is its legacy schema-v1 feed, not a Phase 4 release.
- Version `2.0.2` is the first release prepared by the hardened workflow. It
  builds and tests `linux/amd64` and `linux/arm64`, publishes an immutable exact
  tag, records the index and child-image digests, verifies OCI
  provenance/SBOM attestations, publishes a signed manifest with the GitHub
  Release, and promotes the selected channel feed last. Verify that all three
  public artifacts exist before installing; a source version alone is not a
  published release.
- Stable and beta checks use separate schema-v2 manifests signed with
  channel-scoped Ed25519 keys. Verification is fail-closed by default and the
  signed metadata binds the exact version to the GHCR index/platform digests,
  source commit, release URL, compatibility policy, and monotonic channel
  sequence.
- Signed compatibility accepts only `state_restore_required` or `unsupported`;
  `image_only` is rejected because an old image cannot safely consume state
  written under the newer database writer guard, and `unsupported` blocks the
  managed preflight.
- The committed stable keyring contains only the public
  `stable-release-v1` trust anchor. Its private key is never committed and is
  held in the protected `stable-release` GitHub environment. Beta remains
  fail-closed until a separate beta key is intentionally provisioned.
- The legacy `2.0.1` image has no official update URL configured by default and
  cannot use the new signed preflight. Its first move to `2.0.2` is a manual
  bootstrap that preserves all four mounts. From `2.0.2` onward, DitakNet
  checks the signed channel feed and offers trusted updates automatically.
- An administrator must complete the in-app preflight by typing exact
  `UPDATE X.Y.Z`. DitakNet then re-fetches the signed manifest, checks
  compatibility, creates and validates a target-bound backup, and issues a
  revalidated two-hour receipt with Docker/TrueNAS instructions. DitakNet never
  redeploys its own container.
- Every published version has permanent notes under
  [`release/notes/`](release/notes/); even small fixes and UI/design changes
  must be recorded there before the tag can pass CI.
- A historical GHCR `:latest` alias may exist, but it is unsupported and the
  current workflow never creates or moves it. Always pin an exact SemVer and
  verify its digest.
- Follow [`docs/UPGRADE.md`](docs/UPGRADE.md) and
  [`docs/UPDATE_AND_MIGRATION_SAFETY.md`](docs/UPDATE_AND_MIGRATION_SAFETY.md)
  before changing an installed version.

### Offline-only restore

DitakNet never replaces a live SQLite database. The web process holds an
exclusive mounted database-directory lock for its complete lifetime; the
offline maintenance command acquires that same lock and fails if a lock-aware
DitakNet process still owns the directory. Images from before this lock existed
cannot be detected through it, so every legacy/pre-lock container must still be
explicitly stopped. The Settings page may upload, validate, and show the
generated maintenance command, but it cannot perform a live restore. Setup-time
live restore is also disabled.

For a state-required rollback, keep the failed/new exact image selected until
the recovery database has been restored:

```bash
docker compose stop ditaknet
docker compose run --rm --no-deps --entrypoint python ditaknet \
  -m ditaknet.offline_restore \
  --backup BACKUP.zip \
  --expected-sha256 APPROVED_SHA256 \
  --confirm 'RESTORE BACKUP.zip'
# Only after the command succeeds, select PREVIOUS and start the service.
docker compose up -d
```

The one-shot command validates the archive/hash and saves a pre-offline-restore
snapshot, fsyncing both its file and directory. It checkpoints the stopped
current DB with WAL `TRUNCATE`, removes sidecars, validates/fsyncs the current
and staged databases, and performs one final crash-atomic `os.replace` followed
by a directory fsync. It then writes an external JSON receipt under the backup
mount. It does not initialize or reopen the restored database through
DitakNet's application database layer, rerun migrations, or restamp its schema/
last-writer markers after the bounded integrity check. See the upgrade guide
for the exact tag-change order and TrueNAS equivalent.

Backup ingestion also enforces compressed/uncompressed size, member-count,
per-member, path, checksum, and compression-ratio limits. Web validation sends
blocking ZIP/hash/SQLite inspection to a worker thread instead of holding the
async request loop.

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
