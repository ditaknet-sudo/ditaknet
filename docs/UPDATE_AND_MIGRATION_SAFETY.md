# Update, migration, restore, and rollback safety

DitakNet persists its SQLite database, generated session key, logs, backups,
and plugins outside the container. Replacing a container is reversible only
when the persistent state remains compatible or a verified recovery point is
available.

## Before any state-changing maintenance

1. Record the exact running image tag and digest.
2. Record all persistent mount sources and the network mode.
3. Create and validate a DitakNet backup. For a signed Phase 4 update, do this
   through the administrator preflight by typing exact `UPDATE X.Y.Z`. It
   creates a format-v2 backup, hashes every protected member and the final
   archive, runs SQLite integrity/foreign-key checks, and binds the recovery
   point to the source/target versions, image digest, schema, rollback policy,
   channel, and manifest sequence.
4. Create a filesystem or recursive TrueNAS dataset snapshot while the current
   version is healthy.
5. Keep at least one recovery copy outside the application container and,
   preferably, outside the same storage failure domain.
6. Verify both liveness and deep readiness before establishing the baseline.

```bash
curl -fsS http://HOST:PORT/health
EXPECTED_VERSION=CURRENT HEALTH_URL=http://HOST:PORT/health/deep python - <<'PY'
import json
import os
import urllib.request

with urllib.request.urlopen(os.environ["HEALTH_URL"], timeout=10) as response:
    payload = json.load(response)
assert payload["status"] == "healthy", payload
assert payload["overall_status"] == "pass", payload
assert payload["version"] == os.environ["EXPECTED_VERSION"], payload
assert not payload["failed_checks"], payload
print("deep health baseline verified")
PY
```

## Database and backup rules

- Never copy only a live SQLite main file. WAL data can contain committed
  transactions not yet checkpointed into that file.
- Use DitakNet's backup operation or a storage snapshot that captures a
  mutually consistent state.
- A Phase 4 handoff requires backup format 2 and an auditable `pre_update`
  operation context. Its receipt expires after two hours and is invalidated if
  the archive is missing, changed, corrupt, or no longer matches the recorded
  update. Re-run preflight rather than reusing an invalid receipt.
- On a version transition, DitakNet creates and validates a `pre_migration`
  format-v2 backup before applying database migrations.
- Stop every container that can write the database before replacing/restoring
  `/app/data` or rolling back its dataset.
- Never mount the same writable SQLite dataset into two DitakNet containers.
- Treat backups, logs, and the data directory as secrets containing customer
  network information.
- Validate a restore in an isolated path before using it as the authoritative
  production dataset when practical. Restoration is always offline; neither
  Settings nor first-run setup may replace the live database.

## Offline-only restore contract

The web process holds an exclusive mounted database-directory lock from startup
until its database, scheduler, and plugins have shut down. The one-shot restore
command must acquire that same lock and the same mounted database directory; it
fails closed if a lock-aware DitakNet process remains alive. Legacy images from
before this lock existed cannot be detected through it, so operators must
explicitly stop every legacy/pre-lock container too. Keeping the failed/new
image selected, run the generated command in this order:

```bash
docker compose stop ditaknet
docker compose run --rm --no-deps --entrypoint python ditaknet \
  -m ditaknet.offline_restore \
  --backup BACKUP.zip \
  --expected-sha256 APPROVED_SHA256 \
  --confirm 'RESTORE BACKUP.zip'
# After success only: set DITAKNET_VERSION=PREVIOUS.
docker compose up -d
```

The exact confirmation and expected SHA-256 prevent recovery-point substitution.
The CLI validates the complete backup, takes and validates a
`ditaknet-pre-offline-restore-*.sqlite3` snapshot of the state being replaced,
and fsyncs both the snapshot file and backup directory. It then checkpoints the
stopped current database with `wal_checkpoint(TRUNCATE)`, removes stale/empty
sidecars, validates/fsyncs that self-contained current database and directory,
and validates, hashes, and fsyncs the staged recovery database. One final
`os.replace` and directory fsync form the crash-atomic boundary: before it the
checkpointed current DB is authoritative; after it the recovered DB is.

The CLI then writes an external `offline-restore-receipt-*.json` with both
recovery hashes. It does not invoke the application database layer, migrations,
or marker restamping after its bounded query-only integrity check. This
preserves the restored schema/last-writer evidence for the previous exact
image's own startup compatibility checks.

Do not select/start the previous image before this command succeeds. If
preparation or staging fails before the final replace, the checkpointed current
database remains in place. Keep the failed/new image selected until the failure
is resolved.

ZIP defenses apply before extraction or restore: at most 2 GiB compressed, 256
members, 8 GiB total uncompressed, a bounded compression ratio, per-member size
caps, safe unique paths, format-v2 member hashes, archive SHA-256, and SQLite
integrity/foreign-key checks. Web uploads and validation move blocking ZIP,
hashing, and SQLite inspection to a worker thread instead of holding the async
request loop.

## Trusted update metadata

- Official Phase 4 releases use separate `stable` and `beta` schema-v2 feeds.
  Each manifest is signed with a channel-scoped Ed25519 key and binds the exact
  SemVer image tag to the GHCR index digest and both platform child digests.
- The signed payload also records the source commit, publication time, GitHub
  Release URL, monotonic per-channel sequence, source-version range, target
  database schema, required backup format, and rollback policy.
- Verification is fail-closed by default. Unknown/wrong-channel keys, invalid
  signatures, replayed sequences, cache-policy changes, unsigned fallback data,
  malformed compatibility rules, or mismatched tags/digests cannot authorize a
  handoff.
- The root schema-v1 `update-manifest.json` documents legacy `2.0.1` only. It is
  not a trusted schema-v2 channel release and cannot unlock managed preflight.
- DitakNet never invokes Docker or TrueNAS APIs. The validated receipt exposes
  exact external redeploy/rollback instructions for an administrator to run.

The committed Phase 4 public-key ring is currently empty. Production public
keys, protected-environment private-key secrets, update-feed branch protection,
and the first new SemVer release are external prerequisites; until they exist,
update verification intentionally remains unavailable rather than falling back
to unsigned discovery.

## Upgrade versus migration

Change one risk dimension at a time. A version update, root-to-UID-568 dataset
permission migration, Host Path move, bridge-to-host network change, and reverse
proxy change should not be combined into one untested maintenance action.

For an existing root-owned TrueNAS deployment:

1. backup, snapshot, and stop the app;
2. preserve current ACLs and mount paths in the change record;
3. grant UID/GID `568:568` required access through dataset ACLs;
4. validate create/rename/delete access with temporary files only;
5. start the same compatible application version and validate it;
6. perform the version upgrade in a separate step.

Automatic Permissions is opt-in because it can change top-level ownership. Use
it for a new/empty path; use reviewed ACL migration for existing shared data.

## Rollback policy

Changing only the image tag does not undo database/schema or other persistent
state changes. Schema-v2 signed metadata and managed preflight reject
`image_only`; the only accepted policy values are `state_restore_required` and
`unsupported`. The writer-version guard cannot safely make a tag-only rollback
compatible, so a state-required release must pair the previous image with the
validated pre-update state. An unsupported policy blocks managed preflight and
requires the release-specific recovery runbook.

When compatibility is uncertain, leave the failed/new image selected, stop the
application, restore the approved backup with its offline maintenance command,
and only then select/start the previous image. Alternatively, keep the App
stopped and pair the previous image with a clone or rollback of the mutually
consistent pre-upgrade snapshot. Snapshot rollback discards newer writes;
prefer a recovery clone for validation when capacity allows. Never restore
application data while the new container remains active.

For TrueNAS the receipt order is mandatory: stop the App; recover every
recorded mounted dataset from the recursive pre-update ZFS snapshot clone/
rollback, or run the documented failed/new-image one-shot tool with the exact
same Data and Backups mounts; only then select the previous exact tag; finally
start the App and require passing `/health/deep`. Legacy/pre-lock images must be
explicitly stopped because they do not own the new mounted database-directory
lock.

## Offline recovery

Prepare recovery before relying on it:

- retain the previous exact image in the local registry cache or an authorized
  offline registry/export, with its digest recorded;
- keep the Compose/TrueNAS YAML and non-secret environment settings needed to
  recreate the deployment;
- keep dataset paths, ACLs, UID/GID, port, and network mode in the recovery runbook;
- replicate a verified backup/snapshot outside the failed host or pool;
- retain the generated offline command, approved backup SHA-256, and external
  restore receipt with the change record;
- document how registry credentials and TLS/reverse-proxy settings are restored.

An offline restart should use an already cached exact image (`pull_policy:
missing`). An offline rollback must not depend on `latest`, a mutable tag, or a
GitHub page being reachable.

The GHCR `:latest` alias is unsupported even if a historical alias is visible;
the current release workflow never creates or moves it.

## Post-change acceptance

After upgrade, migration, restore, or rollback, assert the exact expected
version through `/health/deep`, not just that a process answers `/health`.
Confirm database/migrations, scheduler, writable directories, settings, static
assets, and other deep checks report pass; then verify login, inventory, a
controlled monitoring check, logs, and creation of a new validated backup.

Database startup refuses a schema revision newer than the application can read
and refuses an unsafe SemVer downgrade based on the persisted last-writer
version/minimum-reader contract. Migration fingerprints and schema markers are
reported through deep health so an apparent process start cannot hide a state
compatibility failure.

Keep the previous image and recovery points until the rollback window closes.
Follow the detailed operational sequence in [`UPGRADE.md`](UPGRADE.md).
