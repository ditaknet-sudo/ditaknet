# DitakNet — TrueNAS SCALE installation

DitakNet runs as a non-root Docker application on TCP port `5833`. Install an
immutable, exact-version image from GHCR; do not build source on TrueNAS and do
not use a floating tag.

```text
ghcr.io/ditaknet-sudo/ditaknet:2.0.1
```

This tag is shown because it matches the root legacy schema-v1 manifest, but it
is an amd64-only artifact created before the Phase 3/4 hardening and signed
update work. Do not claim that it contains later source changes or use it for
the schema-v2 managed-update handoff. For a new production deployment using the
complete hardened image contract, wait for and verify the next SemVer release,
then replace every example pin consistently.

Before installation, confirm that this exact tag exists and that the GHCR
package is public:

```bash
docker pull ghcr.io/ditaknet-sudo/ditaknet:2.0.1
```

If the pull returns `manifest unknown`, the release image has not been
published. Do not substitute `latest`; a historical alias may exist, but it is
unsupported and the current release workflow never creates or moves it.

## 1. Create production datasets

Host Path datasets are the production storage strategy because they remain
visible for snapshots, replication, recovery, and migration independently of
the app lifecycle. Under a pool such as `tank`, create:

```text
tank/apps/ditaknet
tank/apps/ditaknet/data
tank/apps/ditaknet/logs
tank/apps/ditaknet/backups
tank/apps/ditaknet/plugins
```

The resulting paths are:

```text
/mnt/tank/apps/ditaknet/data
/mnt/tank/apps/ditaknet/logs
/mnt/tank/apps/ditaknet/backups
/mnt/tank/apps/ditaknet/plugins
```

Replace `tank` with the actual pool name. Keep `data`, `logs`, `backups`, and
`plugins` as separate child datasets so each can have an appropriate snapshot,
retention, and replication policy. Never expose these datasets through a
public share; they can contain network inventory, logs, backups, and generated
session secrets.

ixVolumes are acceptable for evaluation through the future catalog form, but
they are not the recommended production layout. Deleting an app can also remove
its ixVolumes.

## 2. Grant non-root storage access

The supplied deployment runs as UID/GID `568:568` (`apps:apps`).
Before deployment, use the TrueNAS dataset ACL editor to give user `568` or
group `568` read/write/execute access to all four child datasets. In the catalog
form, either:

- enable ACL and add the configured run-as UID/GID with Full Control; or
- for a new or empty Host Path, opt in to Automatic Permissions.

Automatic Permissions can change top-level ownership. Create a dataset snapshot
before enabling it on any path that has existing data. The permissions helper
is intentionally not enabled by default for Host Paths. Do not use `chmod 777`
or make the datasets world-writable.

### Migrating an existing root-owned install

1. Create an application backup and a recursive snapshot of
   `tank/apps/ditaknet`.
2. Stop the existing app so SQLite and logs are not being modified.
3. Record the current image tag and the four exact mount paths.
4. Add an ACL entry granting UID or GID `568` Full Control on each child
   dataset. Preserve unrelated ACL entries and shares.
5. Verify that `568:568` can create, rename, and remove a temporary file in each
   dataset, then remove only those temporary files.
6. Deploy the non-root image with the same four mount paths and verify `/health`,
   login, logs, backup creation, and plugin loading.
7. Keep the pre-migration snapshot until the validation period has passed.

If access fails, stop the app and restore the saved ACL or use a clone of the
snapshot for recovery. Do not recursively change ownership without reviewing
other consumers of the datasets.

## 3. Choose networking

| Definition | Use | Exposure |
| --- | --- | --- |
| `truenas/docker-compose.yml` | Default bridge deployment | Publishes container port `5833`; host IP/port can be restricted |
| `truenas/docker-compose.host-network.yml` | Direct LAN discovery, ARP, or ICMP visibility | Shares the TrueNAS network namespace and binds host port `5833` |

Start with bridge networking. Set `DITAKNET_BIND_ADDRESS` to a specific TrueNAS
LAN address when the UI should not listen on every interface. Host networking
does not support port remapping and should be enabled only when bridge-mode
discovery is insufficient. Both definitions drop all Linux capabilities and
restore only `NET_RAW` for ICMP/discovery.

## 4. Install via Custom App YAML

1. Open **Apps → Discover → Install via YAML** (wording varies by TrueNAS
   release).
2. Set the application name to lowercase `ditaknet`.
3. Paste exactly one file from `truenas/`: bridge or host-network.
4. Replace all `/mnt/tank/...` defaults if the pool is not named `tank`.
5. Verify that all four datasets already exist. The supplied bind mounts use
   `create_host_path: false`, so a typo fails deployment instead of silently
   creating a root-owned directory.
6. Confirm the exact image version and save the app.

Pasting YAML in the TrueNAS UI does not use the repository's local `.env` file.
The compose definitions contain safe defaults, but pool paths and optional
bridge bind settings should be made explicit in the pasted YAML. The supplied
hardening contract is:

- non-root `568:568`;
- read-only container root filesystem;
- writable persistent mounts only at `/app/data`, `/app/logs`, `/app/backups`,
  and `/app/plugins`;
- memory-backed `/tmp`;
- `no-new-privileges`, `cap_drop: ALL`, and only `NET_RAW` restored;
- bounded process count and Docker log rotation.

The definitions select `DITAKNET_UPDATE_CHANNEL=stable` and require signed
metadata. Choose `beta` only for an intentional prerelease deployment. Until
the Phase 4 public keyring, protected signing environments, update-feed branch,
and first new SemVer release are provisioned, update checks intentionally fail
closed instead of using unsigned release discovery.

## 5. Validate the installation

Open:

```text
http://TRUENAS-IP:5833
http://TRUENAS-IP:5833/health
```

The liveness endpoint must report `healthy`; `/health/deep` must report a
passing overall status and the exact expected image version. Then complete
initial setup, sign in, run a controlled check against an authorized device,
create and validate a DitakNet backup, and confirm that new
database/log/backup files are owned or writable by UID/GID `568`.

If the app reports `[EFAULT] Failed 'up' action`, inspect Apps logs and, where
available, the lifecycle log:

```bash
sudo tail -n 200 /var/log/app_lifecycle.log
```

## 6. Upgrade and rollback

Never change only the container while forgetting the datasets. Before every
upgrade, refresh **Settings → Updates** and require a trusted schema-v2 manifest.
As an administrator, type exact `UPDATE X.Y.Z`; the preflight validates the
version/digest/compatibility contract and creates a format-v2 target-bound
backup. Record the resulting two-hour receipt, backup SHA-256, old image tag and
digest, then create a recursive TrueNAS dataset snapshot.

The signed rollback policy is never `image_only`; schema-v2 permits only
`state_restore_required` or `unsupported`, and the latter blocks managed
preflight.

Use the receipt's external TrueNAS steps to change only the exact image tag and
redeploy. DitakNet never controls the Apps service. Afterward verify
`/health/deep`, login, state, monitoring, logs, and a new backup. See
[`UPGRADE.md`](UPGRADE.md) for the verified upgrade, rollback, and recovery
sequences, including state restore when the signed policy requires it.

Database restore is offline-only. The Settings page can upload/validate a
backup and display the one-shot command, but neither Settings nor first-run
setup replaces the live database. For `state_restore_required`, use this exact
receipt order:

1. Stop the App and explicitly stop every legacy/pre-lock DitakNet container.
2. Clone/roll back the recorded recursive pre-update ZFS snapshot for all
   mounted datasets, or keep the failed/new image selected and run its generated
   `python -m ditaknet.offline_restore` one-shot command with the exact same Data
   and Backups mounts.
3. Only after recovery succeeds, select the previous exact image.
4. Start the App and require passing `/health/deep`.

The mounted database-directory lifetime lock rejects the maintenance command if
a current lock-aware App is still running. Legacy images predate the lock, which
is why their explicit stop remains mandatory.

The command validates the exact backup SHA-256 and confirmation, fsyncs the
pre-offline backup, checkpoints/fsyncs the stopped current database, fsyncs the
staged recovery database, makes one crash-atomic replacement, writes an external
receipt under the backup dataset, and leaves the recovered database's version/
schema markers untouched. When the exact same Data/Backups mounts cannot be
reproduced safely, leave the App stopped and use the recorded recursive ZFS
recovery clone. Never start the previous image before restoring state.

## Troubleshooting

| Problem | Check |
| --- | --- |
| `manifest unknown` | The exact GHCR tag was not published; never invent a tag |
| Image pull denied | Make the GHCR package public or configure a registry credential |
| Permission denied | Verify all four dataset ACLs grant the configured UID/GID access |
| Missing database after redeploy | Restore the original `/app/data` Host Path; do not initialize a new path |
| Port already in use | Use another bridge host port, or free `5833` before host networking |
| Poor LAN discovery | Configure LAN DNS, then consider the host-network variant |
| Login loop behind HTTPS | Configure the public base URL and secure-cookie settings for the proxy |
| App starts but backup fails | Check both `/app/data` and `/app/backups` permissions and free space |
| Offline restore reports the database directory is in use | Stop every current App using that exact mounted database directory and explicitly stop legacy/pre-lock containers; never bypass the lifetime lock |
