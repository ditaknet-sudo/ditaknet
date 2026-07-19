# TrueNAS official Apps catalog — DitakNet submission pack

This folder prepares DitakNet for a pull request into the official
[`truenas/apps`](https://github.com/truenas/apps) repository
(`ix-dev/community/ditaknet/`).

> **Important:** TrueNAS no longer installs random third-party catalogs in the
> UI. Community apps must be contributed upstream to `truenas/apps`.
> Until that PR is merged, install DitakNet with
> [`../truenas/docker-compose.yml`](../truenas/docker-compose.yml)
> via **Apps → Custom App / Install via YAML** (see
> [`../docs/TRUENAS-INSTALL.md`](../docs/TRUENAS-INSTALL.md)).

## Layout

```text
truenas-catalog/
├── README.md                          ← this file
├── icons/
│   ├── icon.svg                       ← placeholder icon (replace before PR)
│   └── README.md
└── ix-dev/
    └── community/
        └── ditaknet/
            ├── app.yaml
            ├── ix_values.yaml
            ├── questions.yaml
            ├── item.yaml
            ├── README.md
            └── templates/
                ├── docker-compose.yaml
                └── test_values/
                    └── basic-values.yaml
```

## Before opening a PR to truenas/apps

1. Publish at least one stable image:
   `ghcr.io/ditaknet-sudo/ditaknet:2.0.1` (and keep the package **public**).
2. Fork `https://github.com/truenas/apps`.
3. Copy `ix-dev/community/ditaknet/` into your fork under the same path.
4. Upload a final icon to TrueNAS media (or follow current maintainer guidance)
   and update `app.yaml` → `icon:`.
5. Refresh `lib_version` / `lib_version_hash` from the latest
   `truenas/apps` `/library/` release (values in this pack are placeholders
   and **must** be updated before CI will pass).
6. Run the upstream CI helper locally as documented in
   [CONTRIBUTIONS.md](https://github.com/truenas/apps/blob/master/CONTRIBUTIONS.md):
   `python3 .github/scripts/ci.py`
7. Open a PR using the TrueNAS apps PR template. Mention:
   - image source `ghcr.io/ditaknet-sudo/ditaknet`
   - port `5833`
   - capability `NET_RAW`
   - host-network option for LAN discovery
   - persistent mounts: data, logs, backups, plugins

## Quick install today (without catalog merge)

Use the YAML under `../truenas/` — that path is supported on TrueNAS SCALE
24.10+ / 25.x Custom Apps and does not wait for catalog review.
