# DitakNet — TrueNAS SCALE installation

DitakNet runs as a non-root Docker application on TCP port `5833`. Install an
immutable, exact-version image from GHCR; do not build source on TrueNAS and do
not use a floating tag.

```text
ghcr.io/ditaknet-sudo/ditaknet:2.0.1
```

This tag is shown because it matches the current repository manifest, but it is
a legacy amd64 artifact created before the Phase 3 hardening work. Do not claim
that it contains later source changes. For a new production deployment using
the complete hardened image contract, wait for and verify the next SemVer
release, then replace every example pin consistently.

Before installation, confirm that this exact tag exists and that the GHCR
package is public:

```bash
docker pull ghcr.io/ditaknet-sudo/ditaknet:2.0.1
```

If the pull returns `manifest unknown`, the release image has not been
published. Do not substitute `latest`.

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
upgrade, create both a DitakNet backup and a TrueNAS dataset snapshot, record the
old image tag, and then change to the new exact SemVer. See
[`UPGRADE.md`](UPGRADE.md) for the verified upgrade, rollback, and recovery
sequences.

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
