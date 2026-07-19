# Update, migration, restore, and rollback safety

DitakNet persists its SQLite database, generated session key, logs, backups,
and plugins outside the container. Replacing a container is reversible only
when the persistent state remains compatible or a verified recovery point is
available.

## Before any state-changing maintenance

1. Record the exact running image tag and digest.
2. Record all persistent mount sources and the network mode.
3. Create a DitakNet backup and validate that the archive opens and contains a
   valid DitakNet database/manifest.
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
- Stop every container that can write the database before replacing/restoring
  `/app/data` or rolling back its dataset.
- Never mount the same writable SQLite dataset into two DitakNet containers.
- Treat backups, logs, and the data directory as secrets containing customer
  network information.
- Validate a restore in an isolated path before using it as the authoritative
  production dataset when practical.

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

## Image-only rollback warning

Changing only the image tag does not undo database/schema or other persistent
state changes. Use image-only rollback only when release notes explicitly say
the previous version is compatible with state written by the newer version.

When compatibility is uncertain, stop the application and pair the previous
image with a clone or rollback of the pre-upgrade snapshot. Snapshot rollback
discards newer writes; prefer a recovery clone for validation when capacity
allows. Never restore application data while the new container remains active.

## Offline recovery

Prepare recovery before relying on it:

- retain the previous exact image in the local registry cache or an authorized
  offline registry/export, with its digest recorded;
- keep the Compose/TrueNAS YAML and non-secret environment settings needed to
  recreate the deployment;
- keep dataset paths, ACLs, UID/GID, port, and network mode in the recovery runbook;
- replicate a verified backup/snapshot outside the failed host or pool;
- document how registry credentials and TLS/reverse-proxy settings are restored.

An offline restart should use an already cached exact image (`pull_policy:
missing`). An offline rollback must not depend on `latest`, a mutable tag, or a
GitHub page being reachable.

## Post-change acceptance

After upgrade, migration, restore, or rollback, assert the exact expected
version through `/health/deep`, not just that a process answers `/health`.
Confirm database/migrations, scheduler, writable directories, settings, static
assets, and other deep checks report pass; then verify login, inventory, a
controlled monitoring check, logs, and creation of a new validated backup.

Keep the previous image and recovery points until the rollback window closes.
Follow the detailed operational sequence in [`UPGRADE.md`](UPGRADE.md).
