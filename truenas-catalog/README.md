# TrueNAS Apps catalog — DitakNet submission pack

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
                    ├── basic-values.yaml
                    ├── host-network-values.yaml
                    ├── host-path-values.yaml
                    └── host-path-acl-values.yaml
```

`upstream-library.json` at this repository's catalog root locks the official
TrueNAS library version and SHA-256 used by `app.yaml`.

## Local static gate

From the DitakNet repository root:

```bash
python scripts/validate_truenas.py --check-upstream --compose-config
python scripts/render_truenas_catalog.py
pytest -q tests/test_truenas_packaging.py
```

The offline validator rejects duplicate YAML keys, unresolved placeholders,
floating/user-controlled image tags, missing UID/GID `568` storage coverage,
and bridge/host-network contract regressions. `--check-upstream` verifies the
library lock against the official `truenas/apps` `library/hashes.yaml` source.
`--compose-config` normalizes both Custom App definitions but does not start a
container.

`render_truenas_catalog.py` fetches the exact official `truenas/apps` commit
locked in `upstream-library.json`, imports that commit's app library, renders all
four variants, and asserts the generated Compose contracts. It does not pull
or start DitakNet. See [`UPSTREAM_VALIDATION.md`](UPSTREAM_VALIDATION.md).

## Before opening a PR to truenas/apps

This directory is a pre-submission pack. The existing `2.0.1` tag is a legacy
artifact and must not be presented as containing later Phase 3 hardening. Cut a
new SemVer after the complete release gate passes, then update every catalog
version pin before the upstream PR.

1. Publish the new exact stable image and keep the intended public package
   **public**. Record its digest and actual architecture list.
2. Fork `https://github.com/truenas/apps`.
3. Copy `ix-dev/community/ditaknet/` into your fork under the same path.
4. Metadata other than the icon follows the official generator shape. The pack
   uses the repository-hosted SVG while under development; this intentionally
   remains an upstream CI blocker until a TrueNAS reviewer uploads the asset.
   Replace both `app.yaml` and `item.yaml` icon fields with the reviewer-approved
   `https://media.sys.truenas.net/apps/ditaknet/icons/...` URL.
5. Run `python scripts/validate_truenas.py --check-upstream`. If TrueNAS has a
   newer non-v1 library, review `latest_library_index`, pin its exact commit,
   then update `app.yaml` and `upstream-library.json` together from that
   commit's official `library/hashes.yaml`; never validate against a moving
   branch or invent the hash.
6. Run the upstream CI helper locally as documented in
   [CONTRIBUTIONS.md](https://github.com/truenas/apps/blob/master/CONTRIBUTIONS.md):
   each catalog test-values file with `--render-only=true` before deployment
   testing.
7. Open a PR using the TrueNAS apps PR template. Mention:
   - image source `ghcr.io/ditaknet-sudo/ditaknet`
   - port `5833`
   - capability `NET_RAW`
   - host-network option for LAN discovery
   - persistent mounts: data, logs, backups, plugins
   - non-root run-as default and dataset ACL: `568:568`
   - read-only application root filesystem

## Quick install today (without catalog merge)

Use the YAML under `../truenas/` — that path is supported on TrueNAS SCALE
24.10+ / 25.x Custom Apps and does not wait for catalog review.
