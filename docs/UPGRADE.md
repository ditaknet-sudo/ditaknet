# DitakNet upgrade guide (Docker / TrueNAS)

DitakNet **never auto-updates** running containers. Administrators receive an
in-dashboard notice when a newer version is published. Professional access is
complimentary for self-hosted installs — updates are free to apply manually.

## Principles

- Pin an **exact SemVer tag** (for example `2.0.1`). Do **not** use `latest` in production.
- Create a backup before every upgrade.
- Verify `/health` after redeploy.
- Keep a previous tag ready for rollback.

## Image

```text
ghcr.io/ditaknet-sudo/ditaknet:VERSION
```

Example:

```bash
docker pull ghcr.io/ditaknet-sudo/ditaknet:2.0.1
```

## Upgrade steps

1. **Backup**
   - UI: Settings → Updates → Create backup, or Settings → Backups.
   - TrueNAS: snapshot the dataset that holds `data`, `logs`, `backups`, `plugins`.

2. **Change the image tag**
   - Compose / TrueNAS env: set `DITAKNET_VERSION=2.0.1` (exact version).
   - Do not switch production installs to `latest`.

3. **Pull and redeploy**

```bash
docker pull ghcr.io/ditaknet-sudo/ditaknet:2.0.1
docker compose up -d
```

4. **Healthcheck**

```bash
curl -fsS http://127.0.0.1:5833/health
```

Expect `"status":"healthy"`. Then open the dashboard and sign in.

5. **Rollback** (if needed)

```bash
# set DITAKNET_VERSION back to the previous tag, then:
docker pull ghcr.io/ditaknet-sudo/ditaknet:PREVIOUS
docker compose up -d
curl -fsS http://127.0.0.1:5833/health
```

Restore a DitakNet backup from the UI only if the database/schema needs it.

## Update checks

Outbound checks are optional and send **no telemetry** (no hostname, IP, license,
or user data). Configure with:

```text
DITAKNET_UPDATE_CHECK_ENABLED=true
DITAKNET_UPDATE_CHANNEL=stable
DITAKNET_UPDATE_CHECK_INTERVAL_HOURS=6
DITAKNET_UPDATE_MANIFEST_URL=https://raw.githubusercontent.com/ditaknet-sudo/ditaknet/main/update-manifest.json
```

Disable entirely from Settings → Updates, or set `DITAKNET_UPDATE_CHECK_ENABLED=false`.

Manifest: repository root `update-manifest.json`.

Public HTTPS sources (pick one):

- Raw (default): `https://raw.githubusercontent.com/ditaknet-sudo/ditaknet/main/update-manifest.json`
- GitHub Pages (optional): enable Pages on the repo and point `DITAKNET_UPDATE_MANIFEST_URL` to `https://ditaknet-sudo.github.io/ditaknet/update-manifest.json` (or your custom Pages URL)

When no manifest signing key is configured, a failed manifest request may fall
back to the GitHub Releases API for the latest SemVer tag
(`/repos/ditaknet-sudo/ditaknet/releases/latest`). When a signing key is
configured, manifest or signature failure is fail-closed and never uses this
unsigned fallback.

Outbound checks never send hostname, IP, license, user, or other telemetry.
