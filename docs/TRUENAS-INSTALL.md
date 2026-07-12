# DitakNet — TrueNAS SCALE install (simple)

DitakNet monitoring server runs as a Docker app on port **5833**.
Use a **published** image from GHCR — no local source build required.

Recommended image tag for production:

```text
ghcr.io/ditaknet-sudo/ditaknet:2.0.0
```

Use `:latest` only for testing.

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

Replace `tank` with your real pool name.

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
2. Choose **Custom App** / **Install via YAML** (wording depends on TrueNAS version).
3. Paste the contents of `truenas/docker-compose.yml` **or**
   `truenas/docker-compose.host-network.yml`.
4. Set environment variables (or a `.env` based on `truenas/.env.example`):

```text
DITAKNET_VERSION=2.0.0
DITAKNET_DATA_PATH=/mnt/tank/apps/ditaknet/data
DITAKNET_LOGS_PATH=/mnt/tank/apps/ditaknet/logs
DITAKNET_BACKUPS_PATH=/mnt/tank/apps/ditaknet/backups
DITAKNET_PLUGINS_PATH=/mnt/tank/apps/ditaknet/plugins
```

5. Save and start the app.

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

---

## 5. Updates (versioned releases)

1. Create a DitakNet backup from the UI (and optionally snapshot the dataset).
2. Change `DITAKNET_VERSION` to the new SemVer (example: `2.0.1`).
3. Restart / recreate the app so it pulls
   `ghcr.io/ditaknet-sudo/ditaknet:2.0.1`.
4. Recheck `/health` and log in.

Do **not** rely on `:latest` for production.

GitHub releases use tags like `v2.0.0`. The image tag is the same number
**without** the `v` prefix (`2.0.0`).

---

## 6. Troubleshooting

| Problem | What to try |
| --- | --- |
| Image pull denied | Make GHCR package public, or add registry credentials |
| App unhealthy | Check Apps logs; confirm datasets exist and are writable |
| Port already in use | Stop the other service on 5833, or use host-network carefully |
| Poor LAN discovery | Switch to `docker-compose.host-network.yml` |
| Empty UI after update | Confirm you still mount the same `data` path |

More detail: [`TRUENAS.md`](TRUENAS.md).
