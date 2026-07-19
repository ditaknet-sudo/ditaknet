# DitakNet on TrueNAS SCALE

DitakNet is packaged for TrueNAS as a non-root, self-hosted monitoring
container. The WebUI and health endpoint listen on container port `5833`.

- Installation: [`TRUENAS-INSTALL.md`](TRUENAS-INSTALL.md)
- Upgrade and rollback: [`UPGRADE.md`](UPGRADE.md)
- Release tags: [`RELEASES.md`](RELEASES.md)
- Upstream catalog submission pack: [`../truenas-catalog/README.md`](../truenas-catalog/README.md)

## Immutable image contract

Production deployments pin one exact image:

```text
ghcr.io/ditaknet-sudo/ditaknet:2.0.1
```

`2.0.1` matches the current manifest but is a legacy amd64 artifact; later
Phase 3 source changes are not retroactively present in it. The complete
hardened production artifact must be published under a new SemVer and verified
before these defaults are advanced.

Git tag `v2.0.1` corresponds to image tag `2.0.1`. The stable workflow does not
publish or move a floating `latest` tag. A restart should use the locally cached
exact image; an upgrade explicitly selects and pulls a new SemVer.

Ready-made definitions:

- `truenas/docker-compose.yml` — bridge network and configurable host binding;
- `truenas/docker-compose.host-network.yml` — host network, no published ports;
- `truenas/.env.example` — Docker Compose variables for file-based deployment.

## Persistent storage contract

The following paths are the only persistent writable application paths:

| Container path | Contents | Recovery priority |
| --- | --- | --- |
| `/app/data` | SQLite database, generated session key, runtime state | Critical |
| `/app/logs` | Rotating application and audit logs | Operational |
| `/app/backups` | DitakNet backup archives | Critical; replicate separately |
| `/app/plugins` | Optional installed plugins | Match to application version |

Use four child Host Path datasets for production. Snapshot the parent dataset
recursively so the four paths share one recovery point. Do not publish or share
their contents. A backup stored only in `/app/backups` on the same pool is not a
disaster-recovery copy; replicate or export it to another failure domain.

The runtime identity is UID/GID `568:568`. Grant this identity access with
TrueNAS ACL entries or opt-in Automatic Permissions only for a new/empty path.
The complete root-to-non-root migration procedure is in the install guide.

## Container security contract

Both Custom App definitions enforce:

- `user: 568:568`, read-only root filesystem, and memory-backed `/tmp`;
- `no-new-privileges=true` and `privileged: false`;
- all capabilities dropped, with only `NET_RAW` restored;
- fail-fast bind mounts (`create_host_path: false`);
- a process limit, graceful shutdown interval, health check, and log rotation.

The catalog renderer uses the same core privilege controls where the official
TrueNAS app library exposes them: non-root identity, read-only root filesystem,
capability allowlist, `no-new-privileges`, init, and persistent storage. The
direct Custom App YAML additionally sets a PID limit, log rotation, and
`noexec`, `nosuid`, and `nodev` on `/tmp`. An exact catalog image tag retains
Compose's default pull-if-missing behavior even though the library does not
emit an explicit `pull_policy` field.

## Bridge and host networking

Bridge mode is the default and isolates the container network namespace. It can
publish port `5833` on one selected TrueNAS address or another host port. LAN
DNS can be supplied through `DISCOVERY_DNS_SERVERS` when hostname resolution is
needed.

Host-network mode shares the TrueNAS network namespace, has no `ports:` mapping,
and always uses host port `5833`. It can improve LAN discovery, ping, ARP, and
MAC visibility, but it increases exposure and can collide with host services.
Enable it only after bridge-mode validation.

## Runtime configuration and secrets

Do not put passwords, API tokens, session keys, or private keys in checked-in
environment files. If neither `SECRET_KEY` nor `SESSION_SECRET` is supplied,
DitakNet creates a persistent session signing key under `/app/data`; preserving
that dataset preserves existing sessions across redeploys.

Common optional values:

```text
APP_BASE_URL=https://ditaknet.example.com
DISCOVERY_DNS_SERVERS=192.168.1.1
CORS_ALLOWED_ORIGINS=https://ditaknet.example.com
SESSION_COOKIE_SECURE=true
```

Terminate TLS at a trusted reverse proxy and restrict proxy access to the
intended networks. Do not expose the unauthenticated setup flow to the public
Internet.

## Catalog validation

The repository locks the official TrueNAS library version/hash in
`truenas-catalog/upstream-library.json`. Validate metadata, duplicate keys,
security invariants, library provenance, test-value coverage, and both Custom
App Compose files without starting a service:

```bash
python scripts/validate_truenas.py --check-upstream --compose-config
pytest -q tests/test_truenas_packaging.py
```

Before an upstream `truenas/apps` PR, also run its current official renderer for
all four values files: bridge/ixVolume, host-network/ixVolume,
bridge/Host-Path with opt-in permissions, and bridge/Host-Path with ACL-managed
paths. The upstream reviewer must approve and host the final icon.

## Updates

DitakNet never replaces its own container. The administrator chooses the next
exact version after creating an application backup and dataset snapshot. The
full procedure includes health, login, backup, monitoring, and rollback checks;
see [`UPGRADE.md`](UPGRADE.md).
