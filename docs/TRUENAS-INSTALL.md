# DitakNet — TrueNAS SCALE install (simple)

DitakNet monitoring server runs as a Docker app on port **5833**.
Use a **published** image from GHCR — no local source build required.

Recommended image tag for production:

```text
ghcr.io/ditaknet-sudo/ditaknet:2.0.1
```

Use `:latest` only for testing.

Before install, confirm the tag exists:

```bash
docker pull ghcr.io/ditaknet-sudo/ditaknet:2.0.1
```

If pull fails with `manifest unknown`, the GitHub Actions publish for that
version did not finish. Re-run **Publish DitakNet image to GHCR**
(`workflow_dispatch`, version `2.0.1`) on GitHub, then retry.

---

## 1. Create datasets on TrueNAS

In **Datasets**, under your pool (example: `tank`), create:

```text
tank/apps/ditaknet
tank/apps/ditaknet/data
tank/apps/ditaknet/logs
tank/apps/ditaknet/backups
tank/apps/ditaknet/plugins
```

Host paths will look like:

```text
/mnt/tank/apps/ditaknet/data
/mnt/tank/apps/ditaknet/logs
/mnt/tank/apps/ditaknet/backups
/mnt/tank/apps/ditaknet/plugins
```

Replace `tank` with your real pool name (also edit the YAML paths below).

ACL tip: container currently runs as root and can write these paths. If you
later run as apps (`568:568`), give that user write permission on the datasets.

---

## 2. Choose networking

| File | When to use |
| --- | --- |
| `truenas/docker-compose.yml` | Normal install (bridge + published port 5833) |
| `truenas/docker-compose.host-network.yml` | Better LAN discovery / ping / ARP (no `ports:` section) |

Most users can start with the **bridge** file. Switch to **host-network** if
discovery cannot see LAN devices.

---

## 3. Install via YAML

1. Open **Apps → Discover**.
2. Choose **Custom App** / **Install via YAML**.
3. Application name: **`ditaknet`** (not `ditak`).
4. Paste the contents of `truenas/docker-compose.yml` **or**
   `truenas/docker-compose.host-network.yml`.
5. If your pool is not `tank`, replace `/mnt/tank/...` paths in the pasted YAML.
6. Save and start the app.

The YAML pins `ghcr.io/ditaknet-sudo/ditaknet:2.0.1` and absolute host paths —
no `.env` file and no `latest` tag are required for paste-install.

If GHCR asks for login for a private package, make the
`ghcr.io/ditaknet-sudo/ditaknet` package **Public** in GitHub Packages, or
configure a pull credential in TrueNAS.

---

## 4. Open DitakNet

Browser:

```text
http://TRUENAS-IP:5833
```

Health check:

```text
http://TRUENAS-IP:5833/health
```

You should get a healthy JSON/status response. From the TrueNAS shell you can also run:

```bash
curl -sS http://127.0.0.1:5833/health
```

If install fails with `[EFAULT] Failed 'up' action`, read the real error:

```bash
sudo tail -n 200 /var/log/app_lifecycle.log
```

---

## 5. Updates (versioned releases)

1. Create a DitakNet backup from the UI (and optionally snapshot the dataset).
2. Confirm the new tag exists on GHCR (`docker pull ...:NEW`).
3. Edit the Custom App YAML image line to the new SemVer (example: `2.0.2`).
4. Restart / recreate the app.
5. Recheck `/health` and log in.

Do **not** rely on `:latest` for production.

GitHub releases use tags like `v2.0.1`. The image tag is the same number
**without** the `v` prefix (`2.0.1`).

Full upgrade/rollback checklist: [`UPGRADE.md`](UPGRADE.md).

---

## 6. Troubleshooting

| Problem | What to try |
| --- | --- |
| `manifest unknown` / pull failed | Publish the SemVer tag to GHCR; do not invent a tag |
| Image pull denied | Make GHCR package public, or add registry credentials |
| App unhealthy | Check Apps logs; confirm datasets exist and are writable |
| Port already in use | Stop the other service on 5833, or use host-network carefully |
| Poor LAN discovery | Switch to `docker-compose.host-network.yml` |
| Empty UI after update | Confirm you still mount the same `data` path |
| App named `ditak` | Delete/recreate Custom App as `ditaknet` for consistent naming |

More detail: [`TRUENAS.md`](TRUENAS.md).
