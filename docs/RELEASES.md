# DitakNet versioned releases

## Tags

| GitHub release tag | Docker / GHCR image tag | Use |
| --- | --- | --- |
| `v2.0.0` | `ghcr.io/ditaknet-sudo/ditaknet:2.0.0` | **Production / TrueNAS** |
| `v2.0.1` | `ghcr.io/ditaknet-sudo/ditaknet:2.0.1` | Production patch |
| (any) | `ghcr.io/ditaknet-sudo/ditaknet:latest` | **Testing only** |

Rules:

1. Stable customer installs pin an exact SemVer image tag.
2. `latest` moves with every release and must not be used as the only production pin.
3. Creating a Git tag `v*` runs `.github/workflows/publish-ghcr.yml`, which
   builds the image and, **only after a successful build**, pushes both the
   version tag and `latest`.

## Publish a release

```bash
# On the DitakNet monitoring repository (clean main)
git tag -a v2.0.0 -m "DitakNet v2.0.0"
git push origin v2.0.0
```

Then confirm the GitHub Actions run succeeded and the package exists at:

```text
https://github.com/orgs/ditaknet-sudo/packages
# or: https://github.com/ditaknet-sudo/ditaknet/pkgs/container/ditaknet
```

## TrueNAS after a release

Set:

```text
DITAKNET_VERSION=2.0.0
```

Recreate/restart the Custom App so it pulls the new image.
