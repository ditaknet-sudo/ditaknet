# DitakNet

Self-hosted local network monitoring for TrueNAS SCALE.

- Default UI port: **5833**
- Image: `ghcr.io/ditaknet-sudo/ditaknet:<version>`
- Persistent data: `/app/data`, `/app/logs`, `/app/backups`, `/app/plugins`
- Run as: non-root UID/GID `568:568` by default
- Capability: `NET_RAW` (ICMP / discovery)
- Optional host networking for better LAN discovery
- Read-only container root filesystem with writable persistent mounts and `/tmp`

Pin an exact version tag and verify its digest. A historical GHCR `:latest`
alias may exist, but floating tags are unsupported and the current release
workflow never creates or moves it.

Update checks use signature-required, channel-scoped schema-v2 metadata. Stable
and beta feeds have separate Ed25519 keys and bind the advertised release to the
index plus platform digests. An administrator must complete the exact
`UPDATE X.Y.Z` backup preflight, create a TrueNAS snapshot, and then redeploy the
App externally; DitakNet never changes its own container.

Database restore is offline-only. For state rollback, keep the failed/new image
selected, stop the App, and use it in a one-shot maintenance container with the
exact same Data/Backups mounts—or recover every recorded mounted dataset from
the recursive pre-update ZFS snapshot. Explicitly stop legacy/pre-lock
containers because they cannot own the new mounted database-directory lifetime
lock. Select and
start the previous exact image only after state recovery succeeds; Settings and
first-run setup never replace a live database. Signed metadata rejects
`image_only`; policy is limited to `state_restore_required` or `unsupported`.

For production, use four pre-created Host Path datasets and grant the configured
run-as UID/GID full control through the TrueNAS ACL editor. ixVolumes are useful
for evaluation, but deleting an app can also delete its ixVolumes. Never use
world-writable dataset permissions.

Project: https://github.com/ditaknet-sudo/ditaknet
