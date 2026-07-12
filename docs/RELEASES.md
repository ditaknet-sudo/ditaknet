# DitakNet versioned releases

## Tags

| GitHub release tag | Docker / GHCR image tag | Use |
| --- | --- | --- |
| `v2.0.1` | `ghcr.io/ditaknet-sudo/ditaknet:2.0.1` | **Production / TrueNAS (current)** |
| `v2.0.0` | `ghcr.io/ditaknet-sudo/ditaknet:2.0.0` | Previous stable |
| (any) | `ghcr.io/ditaknet-sudo/ditaknet:latest` | **Testing only** |

Rules:

1. Stable customer installs pin an exact SemVer image tag.
2. `latest` moves with every release and must not be used as the only production pin.
3. Creating a Git tag `v*` runs `.github/workflows/publish-ghcr.yml`, which
   runs tests, builds the image, and **only after success** pushes the version
   tag and `latest`.
4. Never retag or overwrite an already published version such as `2.0.0`.

## Publish a release

```bash
git tag -a v2.0.1 -m "DitakNet v2.0.1"
git push origin v2.0.1
```

Then confirm the GitHub Actions run succeeded and the package exists.

## TrueNAS after a release

Set:

```text
DITAKNET_VERSION=2.0.1
```

Recreate/restart the Custom App so it pulls the new image.
