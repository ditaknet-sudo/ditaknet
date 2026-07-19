# DitakNet upgrade and rollback guide

DitakNet does not auto-update a running container. An administrator explicitly
selects an exact GHCR SemVer, preserves a recovery point, redeploys, and verifies
the application.

## Rules

- Use `ghcr.io/ditaknet-sudo/ditaknet:X.Y.Z`; never use `latest`.
- Read the release notes and confirm the exact tag exists before maintenance.
- Create both a DitakNet backup and a storage-level snapshot before upgrade.
- Keep the previous exact image locally until the validation period ends.
- Do not run two DitakNet containers against the same writable SQLite dataset.
- Never restore a database while either old or new container is writing to it.

## Pre-upgrade record

Record these values in the change ticket or maintenance log:

```text
Current image tag and digest:
Target image tag and digest:
Deployment type: Docker Compose / TrueNAS Custom App / TrueNAS catalog
Network mode: bridge / host
Host WebUI address and port:
Data dataset/path:
Logs dataset/path:
Backups dataset/path:
Plugins dataset/path:
Pre-upgrade backup filename:
Pre-upgrade snapshot name:
```

For TrueNAS, snapshot the parent `.../ditaknet` dataset recursively so all four
child datasets represent the same recovery point. Replicate or export the
backup when the pool itself is part of the risk being mitigated.

## Upgrade sequence

1. **Check the target release**

   Confirm the GitHub release, release notes, supported upgrade path, GHCR tag,
   and successful release workflow. Pull the exact target without changing the
   running container:

   ```bash
   docker pull ghcr.io/ditaknet-sudo/ditaknet:TARGET
   docker image inspect ghcr.io/ditaknet-sudo/ditaknet:TARGET
   ```

2. **Create recovery points**

   Create and validate a DitakNet backup from the UI. Then create the recursive
   TrueNAS snapshot or equivalent filesystem snapshot. Record both names.

3. **Record baseline behavior**

   Verify `/health` and `/health/deep`, login, one authorized monitoring check,
   recent logs, free space, and backup availability before changing anything.

   If the deployment was created with an older repository Compose file, finish
   the [legacy repository Compose migration](#legacy-repository-compose-migration)
   below before continuing. Do not assume the new storage defaults point to the
   old database.

4. **Select the target exact version**

   For the repository Compose deployment, set `DITAKNET_VERSION=TARGET` in the
   local uncommitted `.env` file. Preserve any existing `DITAKNET_*_SOURCE`
   values, then render the configuration and verify all four mount sources
   before starting:

   ```bash
   docker compose config
   docker compose pull
   docker compose up -d
   ```

   Do not use `--no-deps`: named-volume deployments require the scoped
   `storage-init` dependency to migrate ownership before the non-root service
   starts. Operator bind paths are never mounted by that initializer and must
   already grant UID/GID `568:568` access.

   For a TrueNAS Custom App, edit only the exact image tag in the saved YAML,
   preserve all four Host Paths and the selected network mode, and redeploy.
   For a future catalog install, select the catalog update after reviewing its
   application version.

5. **Validate before closing maintenance**

   ```bash
   curl -fsS http://HOST:PORT/health
   EXPECTED_VERSION=TARGET HEALTH_URL=http://HOST:PORT/health/deep python - <<'PY'
   import json
   import os
   import urllib.request

   with urllib.request.urlopen(os.environ["HEALTH_URL"], timeout=10) as response:
       payload = json.load(response)
   assert payload["status"] == "healthy", payload
   assert payload["overall_status"] == "pass", payload
   assert payload["version"] == os.environ["EXPECTED_VERSION"], payload
   assert not payload["failed_checks"], payload
   print("deep health and version verified")
   PY
   ```

   Confirm all of the following:

   - liveness reports `healthy`, deep health reports `pass`, and its `version`
     equals the exact target tag;
   - the existing administrator can sign in and setup is not shown again;
   - existing devices, services, alerts, users, and settings are present;
   - one controlled ping/TCP/HTTP check completes;
   - new logs are written without permission errors;
   - a new backup can be created and validated;
   - the container still runs as UID/GID `568:568` with only `NET_RAW`;
   - the original four persistent mounts are still attached.

6. **Retain recovery assets**

   Keep the old image, pre-upgrade backup, and snapshot for the documented
   rollback window. Delete them only under the normal retention policy.

## Legacy repository Compose migration

Repository Compose versions before the Phase 3 packaging changes mounted
`./data`, `./logs`, `./backups`, and `./plugins`. The newer file intentionally
uses Docker named volumes for safe fresh installs. Updating the YAML without
preserving the four old bind sources starts against empty volumes and can look
like data loss even though the old files remain on disk.

Perform this once, after the backup/snapshot and while the old deployment is
stopped:

1. Record the current mount type and source for every `/app/*` destination:

   ```bash
   docker inspect ditaknet-monitoring \
     --format '{{range .Mounts}}{{println .Destination "<-" .Source "(" .Type ")"}}{{end}}'
   docker compose down
   ```

2. Resolve the old four repository paths to absolute host paths. Put them in
   the local, uncommitted `.env` next to `docker-compose.yml`:

   ```dotenv
   DITAKNET_DATA_SOURCE=/absolute/path/to/checkout/data
   DITAKNET_LOGS_SOURCE=/absolute/path/to/checkout/logs
   DITAKNET_BACKUPS_SOURCE=/absolute/path/to/checkout/backups
   DITAKNET_PLUGINS_SOURCE=/absolute/path/to/checkout/plugins
   DITAKNET_VERSION=TARGET
   ```

3. On Linux, grant the non-root runtime ownership of those dedicated DitakNet
   paths. Verify every resolved path before running this command; never apply it
   to a shared directory or a broad parent:

   ```bash
   sudo chown -R 568:568 \
     /absolute/path/to/checkout/data \
     /absolute/path/to/checkout/logs \
     /absolute/path/to/checkout/backups \
     /absolute/path/to/checkout/plugins
   ```

   On TrueNAS, use the dataset ACL procedure instead. Do not use `chmod 777`.

4. Run `docker compose config` and confirm the rendered sources still identify
   the four recorded old paths. Then use the normal `docker compose pull` and
   `docker compose up -d` sequence. After startup, inspect mounts again and
   confirm `/health/deep` reports the expected version and existing data.

For a genuinely new installation, leave the four variables unset and use the
named-volume defaults. Do not copy a live SQLite file from a legacy bind path
into a named volume; use a stopped, mutually consistent dataset copy or the
validated backup/restore process.

## Rollback decision

Use a **container-only rollback** only when release notes state that the schema
and persistent state remain compatible with the previous release. Otherwise,
restore or clone the pre-upgrade storage snapshot together with the previous
image. Starting old code against state already migrated by newer code can cause
secondary failures.

### Container-only rollback

1. Stop the new container and retain its logs.
2. Set `DITAKNET_VERSION=PREVIOUS` or restore the previous exact Custom App
   image tag.
3. Redeploy without changing any mount path.
4. Verify health, login, inventory, checks, logs, and backup creation.
5. Repeat the `/health/deep` assertion with `EXPECTED_VERSION=PREVIOUS`.

```bash
docker compose down
# Set DITAKNET_VERSION=PREVIOUS in the local environment file.
docker compose up -d
curl -fsS http://HOST:PORT/health
```

### State rollback or recovery clone

A snapshot rollback replaces newer writes and is destructive. Prefer cloning
the pre-upgrade snapshot to recovery datasets first when capacity allows.

1. Stop DitakNet and verify no container has the four datasets mounted writable.
2. Preserve failed-version logs and, if useful, snapshot the failed state under
   a clearly different name.
3. Clone the pre-upgrade snapshot or explicitly approve a snapshot rollback.
4. Point all four mounts to the mutually consistent recovery datasets.
5. Pin the previous exact image and start one container only.
6. Verify health and application data before making the recovery deployment
   authoritative.

If using an application backup instead of a dataset snapshot, start the
compatible application with a fresh isolated data path and use the supported
restore flow. Never copy a live SQLite database file or mix a restored `data`
dataset with mismatched plugin state without validation.

## UID/GID 568 migration during upgrade

If upgrading from a root-running image to the non-root runtime, follow the
backup-first ACL migration in [`TRUENAS-INSTALL.md`](TRUENAS-INSTALL.md). Do not
combine an untested permission migration, network-mode change, dataset move,
and application version upgrade in one maintenance action. Establish the
`568:568` storage contract first, validate it, then upgrade the version.

## Update checks

Outbound release checks are optional and send no hostname, IP, license, user,
or monitoring telemetry. Configuration:

```text
DITAKNET_UPDATE_CHECK_ENABLED=true
DITAKNET_UPDATE_CHANNEL=stable
DITAKNET_UPDATE_CHECK_INTERVAL_HOURS=6
DITAKNET_UPDATE_MANIFEST_URL=https://raw.githubusercontent.com/ditaknet-sudo/ditaknet/main/update-manifest.json
```

When a manifest signing key is configured, manifest/signature failure is
fail-closed and the unsigned GitHub Releases fallback is disabled. Without a
signing key, a failed manifest request may use the public GitHub Releases API to
discover the latest SemVer. Update checks notify only; they never redeploy the
container.
