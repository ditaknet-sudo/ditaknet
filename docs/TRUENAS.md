# DitakNet on TrueNAS SCALE

DitakNet is designed to run as a self-hosted container with persistent storage
owned by the TrueNAS system. The application listens on port `5833`.

**Quick install (YAML):** see [`TRUENAS-INSTALL.md`](TRUENAS-INSTALL.md).  
**Version tags:** see [`RELEASES.md`](RELEASES.md).  
**Official catalog pack:** see [`../truenas-catalog/README.md`](../truenas-catalog/README.md).

## Image

Publish a versioned image before installing through TrueNAS:

```text
ghcr.io/ditaknet-sudo/ditaknet:2.0.1
```

Use immutable version tags for production. Use `latest` only for testing.

Ready-made Custom App compose files:

- `truenas/docker-compose.yml` — bridge networking + port `5833`
- `truenas/docker-compose.host-network.yml` — host networking (no `ports:`)
- `truenas/.env.example` — path / version variables

## Required Mounts

Mount these directories from a dataset controlled by the installer:

```text
/app/data     SQLite database, generated session signing key, runtime state
/app/logs     rotating application logs
/app/backups  backup archives created by DitakNet
/app/plugins  optional user-installed plugins
```

Do not publish or share the contents of these directories. They can contain
customer network data, logs, backups, and generated secrets.

## Runtime Configuration

Non-secret Docker defaults are in `config/runtime.env`. Do not put passwords,
session keys, API tokens, or private keys in environment files.

DitakNet stores user passwords only as database hashes. If no external
`SECRET_KEY` or `SESSION_SECRET` is supplied, the app generates a persistent
session signing key under `/app/data`.

Useful optional settings:

```text
APP_BASE_URL=https://ditaknet.example.com
DISCOVERY_DNS_SERVERS=192.168.1.1
CORS_ALLOWED_ORIGINS=https://ditaknet.example.com
SESSION_COOKIE_SECURE=true
```

For Docker bridge networking, `DISCOVERY_DNS_SERVERS` should usually point to
the LAN router or DNS server if PTR/hostname discovery is needed.

## Networking

The default web port is:

```text
5833/tcp
```

LAN discovery may need additional network permissions depending on the TrueNAS
App configuration. Raw ping and ARP visibility can be limited by container
networking. On Linux/TrueNAS, host networking (`truenas/docker-compose.host-network.yml`)
gives the most accurate LAN discovery when allowed by the deployment policy.
Both compose variants add `NET_RAW`.

## Updates

Before updating:

1. Create a DitakNet backup from the UI.
2. Snapshot the TrueNAS dataset that contains `data`, `logs`, `backups`, and
   optional `plugins`.
3. Change `DITAKNET_VERSION` to the new SemVer and pull that image tag.
4. Restart the app.
5. Verify `http://HOST:5833/health` returns healthy.

## Public Repository Checklist

Before publishing to GitHub or submitting for TrueNAS review:

- Do not include `data/`, `logs/`, `backups/`, local `.env` files, caches, or
  screenshots.
- Keep only non-secret defaults in `config/runtime.env`.
- Publish legal documents from `docs/legal/` when present.
- Publish a versioned image through the GHCR workflow before referencing it in
  TrueNAS materials.
- For the official catalog, copy `truenas-catalog/ix-dev/community/ditaknet/`
  into a fork of `truenas/apps` and refresh `lib_version` / icon URL.
