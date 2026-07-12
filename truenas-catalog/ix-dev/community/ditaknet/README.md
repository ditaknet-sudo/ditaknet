# DitakNet

Self-hosted local network monitoring for TrueNAS SCALE.

- Default UI port: **5833**
- Image: `ghcr.io/ditaknet-sudo/ditaknet:<version>`
- Persistent data: `/app/data`, `/app/logs`, `/app/backups`, `/app/plugins`
- Capability: `NET_RAW` (ICMP / discovery)
- Optional host networking for better LAN discovery

Pin a version tag in production. Use `latest` only for testing.

Project: https://github.com/ditaknet-sudo/ditaknet
