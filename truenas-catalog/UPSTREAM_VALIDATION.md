# Pinned upstream TrueNAS render evidence

Validation date: **2026-07-20**

Official source:

```text
Repository: https://github.com/truenas/apps.git
Commit: f733713ecfda1d683043775e6d9cc8f09545e1b3
Library: 2.3.8
Library SHA-256: cd75c897a1e8fef54b5bd00d0d8849f240bc50db2ef650eccc0ee74f3b2b2dc1
```

Command:

```bash
python scripts/render_truenas_catalog.py
```

Result:

```text
Pinned official TrueNAS render OK: f733713ecfda1d683043775e6d9cc8f09545e1b3
  - basic-values.yaml
  - host-network-values.yaml
  - host-path-values.yaml
  - host-path-acl-values.yaml
```

The render caught and corrected two catalog-library incompatibilities before
this result: TrueNAS' tmpfs mode validator requires a leading-zero octal mode,
and its environment helper already injects `TZ`, so a duplicate explicit `TZ`
is rejected.

Assertions cover:

- non-root `568:568`, read-only root filesystem, `no-new-privileges`, and only
  `NET_RAW` after `cap_drop: ALL`;
- all four persistent mounts plus writable `/tmp`;
- bridge port `5833 → 5833` and custom host port `15833 → 5833`;
- host network with no published port or bridge portal;
- official permissions helper rendering and dependency ordering for ixVolume
  and opt-in Host Path automatic permissions, plus helper suppression for
  ACL-managed Host Paths.

This deterministic renderer uses official library code from the pinned commit
without starting an application server. Before an upstream PR, additionally
run that commit's complete `apps_validation` render/schema tooling and current
maintainer checks. The upstream helper currently references its validation
container with a floating tag, so a release CI integration must resolve and pin
that helper image digest before execution.

The development pack deliberately keeps its repository-hosted SVG icon. The
official metadata generator requires a reviewer-provided TrueNAS media URL, so
the full upstream metadata gate cannot pass until that external asset upload is
complete and both metadata files use the approved URL.
