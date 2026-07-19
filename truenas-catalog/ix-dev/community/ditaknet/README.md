# DitakNet

Self-hosted local network monitoring for TrueNAS SCALE.

- Default UI port: **5833**
- Image: `ghcr.io/ditaknet-sudo/ditaknet:<version>`
- Persistent data: `/app/data`, `/app/logs`, `/app/backups`, `/app/plugins`
- Run as: non-root UID/GID `568:568` by default
- Capability: `NET_RAW` (ICMP / discovery)
- Optional host networking for better LAN discovery
- Read-only container root filesystem with writable persistent mounts and `/tmp`

Pin an exact version tag. Floating tags are unsupported by the stable release workflow.

For production, use four pre-created Host Path datasets and grant the configured
run-as UID/GID full control through the TrueNAS ACL editor. ixVolumes are useful
for evaluation, but deleting an app can also delete its ixVolumes. Never use
world-writable dataset permissions.

Project: https://github.com/ditaknet-sudo/ditaknet
