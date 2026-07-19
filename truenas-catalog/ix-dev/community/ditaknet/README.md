# DitakNet

Self-hosted local network monitoring for TrueNAS SCALE.

- Default UI port: **5833**
- Image: `ghcr.io/ditaknet-sudo/ditaknet:<version>`
- Persistent data: `/app/data`, `/app/logs`, `/app/backups`, `/app/plugins`
- Capability: `NET_RAW` (ICMP / discovery)
- Optional host networking for better LAN discovery

Pin an exact version tag. Floating tags are unsupported by the stable release workflow.

Project: https://github.com/ditaknet-sudo/ditaknet
