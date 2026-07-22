# DitakNet upgrade and rollback guide

DitakNet does not auto-update a running container. An administrator explicitly
selects a trusted signed release, completes the backup-first preflight, then
redeploys the exact GHCR SemVer from Docker or TrueNAS and verifies the
application.

## Rules

- Use `ghcr.io/ditaknet-sudo/ditaknet:X.Y.Z`; never use `latest`.
- Require a fresh channel-scoped Ed25519 schema-v2 manifest and verify the
  target index digest. The root schema-v1 `update-manifest.json` and legacy
  `2.0.1` artifact are not sufficient for the managed handoff.
- Read the release notes and signed compatibility contract before maintenance.
- Complete the admin preflight and create a storage-level snapshot before
  redeploying. The preflight itself creates and validates a format-v2 DitakNet
  backup bound to the target version, digest, schema, channel, and sequence.
- Keep the previous exact image locally until the validation period ends.
- Do not run two DitakNet containers against the same writable SQLite dataset.
- Never restore a database while either old or new container is writing to it.
- Live restore is disabled in both Settings and first-run setup. Settings may
  validate a backup and generate the offline command, but database replacement
  always happens in a stopped, one-shot maintenance container.

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
Pre-upgrade backup SHA-256:
Preflight receipt ID and expiry:
Pre-upgrade snapshot name:
```

For TrueNAS, snapshot the parent `.../ditaknet` dataset recursively so all four
child datasets represent the same recovery point. Replicate or export the
backup when the pool itself is part of the risk being mitigated.

## Upgrade sequence

1. **Check the target release and complete the admin preflight**

   In **Settings → Updates**, refresh the selected `stable` or `beta` channel.
   Confirm the manifest is trusted, its schema is version 2, the exact GHCR tag
   and index/platform digests are shown, and the compatibility/rollback policy
   permits the current version. Type exactly `UPDATE TARGET` as an administrator.

   DitakNet force-fetches the signed metadata, checks the monotonic channel
   sequence and compatibility contract, creates and immediately revalidates a
   format-v2 `pre_update` backup, and stores an auditable handoff receipt. The
   receipt remains usable for at most two hours and is revalidated when opened.
   If it expires, the backup is missing/changed, or the release identity changes,
   run the preflight again. Do not construct substitute commands from untrusted
   UI text.

   Confirm the GitHub Release contains the byte-identical signed manifest and
   the release workflow succeeded. Use the exact image/digest commands returned
   by the receipt. A typical independent pull/inspection is:

   ```bash
   docker pull ghcr.io/ditaknet-sudo/ditaknet:TARGET
   docker image inspect ghcr.io/ditaknet-sudo/ditaknet:TARGET
   ```

2. **Create recovery points**

   Record the preflight-created, validated backup name and SHA-256. Then create
   the recursive TrueNAS snapshot or equivalent filesystem snapshot and record
   both recovery points. Do not replace the target-bound preflight backup with
   an older generic archive.

3. **Record baseline behavior**

   Verify `/health` and `/health/deep`, login, one authorized monitoring check,
   recent logs, free space, and backup availability before changing anything.

   If the deployment was created with an older repository Compose file, finish
   the [legacy repository Compose migration](#legacy-repository-compose-migration)
   below before continuing. Do not assume the new storage defaults point to the
   old database.

4. **Select the target exact version**

   Follow the still-valid receipt's external handoff instructions. DitakNet does
   not call Docker, edit Compose, or control the TrueNAS Apps service.

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

   Startup also enforces the database's last-writer SemVer, schema revision,
   minimum reader, and migration fingerprint. A future-schema database or an
   unsafe application downgrade is refused rather than modified. When a version
   transition can migrate state, DitakNet creates and validates a format-v2
   `pre_migration` backup before applying migrations; `/health/deep` reports the
   resulting compatibility markers.

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
validated offline restore process.

## Restore safety boundary

The DitakNet web process owns an exclusive mounted database-directory lock for
its entire lifetime, including scheduler and plugin activity. The offline
restore CLI acquires the same lock non-blockingly. If a current DitakNet web
process or another maintenance command still owns that mounted database
directory, the restore fails closed before replacing the database. Images from
before this lock was introduced cannot advertise their activity through it, so
explicitly stopping every legacy/pre-lock container is still mandatory. The
one-shot command must mount the same `/app/data` and `/app/backups` as the
stopped deployment.

The Settings backup page can upload, validate, and display the generated
offline command. It has no live restore submit action. First-run setup cannot
restore a database in-process either; stage the approved backup in the backup
mount and perform the same stopped-container maintenance before starting the
application.

Before replacement, the CLI saves and validates the current database as
`ditaknet-pre-offline-restore-*.sqlite3`, then fsyncs both that file and its
backup directory. It checkpoints the stopped current database with
`wal_checkpoint(TRUNCATE)`, removes its stale/empty WAL/SHM sidecars, validates
and fsyncs the self-contained current database, and fsyncs its directory. The
recovered database is staged, validated, hashed, and fsynced before one final
crash-atomic `os.replace`; the database directory is fsynced afterward. Thus a
crash before the replace leaves the checkpointed current database authoritative,
while a crash after it leaves the staged recovery database authoritative.

After replacement the CLI writes an `offline-restore-receipt-*.json` file
outside SQLite in the backup mount. The receipt records the approved archive
hash, restored database hash, backup format/app version, and the pre-offline
snapshot name/hash. The CLI performs bounded query-only integrity validation
but deliberately does not reopen the database through DitakNet's application
database layer, run migrations, or restamp last-writer/schema markers. The
previous exact image therefore sees the recovery point's original compatibility
markers when it starts.

Backup upload and ZIP validation are resource-bounded: the compressed upload is
capped at 2 GiB; ZIPs are limited to 256 members, 8 GiB total uncompressed data,
a 200:1 compression ratio for large members, and member-specific size caps.
Duplicate names, unsafe paths, excess/corrupt members, checksum mismatches, and
invalid SQLite content are rejected. Web upload/validation performs blocking
archive and SQLite inspection on a worker thread so the event loop is not held
by a large archive.

## Rollback decision

Schema-v2 signed metadata accepts only `state_restore_required` or
`unsupported`. `image_only` is rejected because the database writer-version
guard cannot safely prove that newer persistent state is readable by old code.
Managed preflight therefore never authorizes a tag-only rollback.

For `state_restore_required`, restore or clone the pre-upgrade storage snapshot
or validated preflight backup while the App is stopped. When using the backup
CLI, keep the failed/new image selected until restore succeeds and only then
select the previous exact image. An `unsupported` policy blocks managed
preflight; keep the service stopped and follow the release-specific recovery
runbook. Starting old code directly against state written by newer code is
unsafe and is also blocked when the last-writer/schema contract proves the
downgrade invalid.

### State rollback from a DitakNet backup — offline only

Do not select or start the previous image first. Keep the failed/new exact image
selected so its maintenance code performs the restore, and use the filename,
SHA-256, and command that were validated before the failed upgrade:

```bash
docker compose stop ditaknet
docker compose run --rm --no-deps --entrypoint python ditaknet \
  -m ditaknet.offline_restore \
  --backup BACKUP.zip \
  --expected-sha256 APPROVED_SHA256 \
  --confirm 'RESTORE BACKUP.zip'
```

The confirmation must exactly match the backup basename. Verify the command's
JSON result reports `"ok": true`, preserve the named pre-offline snapshot and
external receipt, and do not continue on any lock/hash/validation failure. Only
after the offline restore succeeds:

1. Set `DITAKNET_VERSION=PREVIOUS` or select the previous exact TrueNAS image.
2. Run `docker compose up -d` or start the TrueNAS App.
3. Verify `/health/deep` reports the previous version and passing schema/
   migration markers, then verify login, data, monitoring, logs, and a new
   backup.

If preparation or staging fails before the final `os.replace`, the checkpointed
current database remains in place and authoritative. Do not change the image
tag until the failure is understood.

### Dataset snapshot rollback or recovery clone

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

On TrueNAS, the mandatory receipt order is: stop the App; recover all recorded
mounted datasets from the recursive pre-update ZFS snapshot clone/rollback, or
run the documented failed/new-image one-shot container with the exact same Data
and Backups mounts; only after recovery succeeds select the previous exact tag;
then start and verify `/health/deep`. If the TrueNAS UI cannot reproduce the
exact maintenance mounts, use a snapshot clone rather than inventing a partial
container command. Never copy a live SQLite database file or mix a restored
`data` dataset with mismatched plugin state without validation.

## UID/GID 568 migration during upgrade

If upgrading from a root-running image to the non-root runtime, follow the
backup-first ACL migration in [`TRUENAS-INSTALL.md`](TRUENAS-INSTALL.md). Do not
combine an untested permission migration, network-mode change, dataset move,
and application version upgrade in one maintenance action. Establish the
`568:568` storage contract first, validate it, then upgrade the version.

## Update checks

Outbound release checks are optional and send no hostname, IP, license, user,
or monitoring telemetry. The official stable and beta URLs are selected by the
channel; do not point production clients at `main/update-manifest.json`.
Configuration:

```text
DITAKNET_UPDATE_CHECK_ENABLED=true
DITAKNET_UPDATE_CHANNEL=stable
DITAKNET_UPDATE_CHECK_INTERVAL_HOURS=6
DITAKNET_UPDATE_SIGNATURE_REQUIRED=true
```

The default feeds are
`https://raw.githubusercontent.com/ditaknet-sudo/ditaknet/update-feed/stable.json`
and `.../beta.json`. A custom `DITAKNET_UPDATE_MANIFEST_URL` is an explicit
operator override, but it does not relax signature, channel, digest, or
anti-replay validation.

Signature verification is required and fail-closed by default: unknown key,
wrong channel, invalid signature, stale/replayed sequence, invalid digest, or
network failure cannot fall back to unsigned GitHub release metadata or unlock
the preflight. The source tree currently ships an empty public-key ring until
the first Phase 4 release keys and protected environments are externally
provisioned, so a managed handoff is intentionally unavailable before then.
Update checks notify and prepare auditable external instructions only; they
never redeploy the container.
