#!/usr/bin/env python3
"""Offline-safe signing and verification for DitakNet update manifests.

Private keys are accepted only as canonical base64 in an environment variable
or a file.  They are never accepted as command-line values and never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ditaknet.core.update_metadata import (  # noqa: E402
    public_key_base64,
    sign_manifest,
    validate_update_manifest,
    verified_signature_key_ids,
)


DEFAULT_PRIVATE_KEY_ENV = "DITAKNET_UPDATE_SIGNING_PRIVATE_KEY"


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"could not read {label} file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} file {path} is not valid JSON: {exc}") from exc


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _private_key(args: argparse.Namespace) -> str:
    if args.private_key_file is not None:
        try:
            value = args.private_key_file.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError) as exc:
            raise ValueError(
                f"could not read private-key file {args.private_key_file}: {exc}"
            ) from exc
        source = f"file {args.private_key_file}"
    else:
        source = f"environment variable {args.private_key_env}"
        value = os.environ.get(args.private_key_env, "").strip()
    if not value:
        raise ValueError(f"private key is missing from {source}")
    return value


def _load_keyring(path: Path) -> dict[str, dict[str, str]]:
    raw = _read_json(path, "keyring")
    if not isinstance(raw, dict):
        raise ValueError("keyring must be a JSON object")
    keyring: dict[str, dict[str, str]] = {}
    for channel, keys in raw.items():
        if channel not in {"stable", "beta"} or not isinstance(keys, dict):
            raise ValueError("keyring channels must be stable or beta objects")
        normalized: dict[str, str] = {}
        for key_id, public_key in keys.items():
            if not isinstance(key_id, str) or not isinstance(public_key, str):
                raise ValueError("keyring key IDs and public keys must be strings")
            normalized[key_id] = public_key
        keyring[channel] = normalized
    return keyring


def _command_sign(args: argparse.Namespace) -> int:
    manifest = _read_json(args.input, "manifest")
    signed = sign_manifest(
        manifest,
        key_id=args.key_id,
        private_key=_private_key(args),
    )
    _write_json_atomic(args.output, signed)
    print(
        f"signed {signed['channel']} manifest with key {args.key_id} -> {args.output}"
    )
    return 0


def _command_build(args: argparse.Namespace) -> int:
    policy = _read_json(args.policy, "update policy")
    if not isinstance(policy, dict):
        raise ValueError("update policy must be a JSON object")
    manifest = {
        "schema_version": 2,
        "channel": args.channel,
        "version": args.version,
        "docker_image": f"ghcr.io/ditaknet-sudo/ditaknet:{args.version}",
        "image_digest": args.image_digest,
        "platform_digests": {
            "linux/amd64": args.amd64_digest,
            "linux/arm64": args.arm64_digest,
        },
        "release_url": (
            f"https://github.com/ditaknet-sudo/ditaknet/releases/tag/v{args.version}"
        ),
        "source_commit": args.source_commit,
        "published_at": args.published_at,
        "sequence": args.sequence,
        "compatibility": policy,
        "critical": bool(args.critical),
        "changelog_url": (
            "https://github.com/ditaknet-sudo/ditaknet/blob/"
            f"v{args.version}/CHANGELOG.md"
        ),
        "release_notes": args.release_notes
        or f"DitakNet {args.version} signed container release.",
        "message": {
            "en": f"DitakNet {args.version} is available",
            "hy": f"Հասանելի է DitakNet {args.version} թարմացումը",
            "ru": f"Доступно обновление DitakNet {args.version}",
        },
        "upgrade_hint": {
            "en": "Complete the backup-first preflight before changing the exact image tag.",
            "hy": "Exact image tag-ը փոխելուց առաջ ավարտեք backup-first preflight-ը։",
            "ru": "Перед сменой точного тега образа выполните backup-first preflight.",
        },
    }
    validated = validate_update_manifest(manifest, require_signatures=False)
    _write_json_atomic(args.output, validated)
    print(
        f"built unsigned {validated['channel']} manifest "
        f"{validated['version']} -> {args.output}"
    )
    return 0


def _command_verify(args: argparse.Namespace) -> int:
    manifest = _read_json(args.input, "manifest")
    keyring = _load_keyring(args.keyring)
    validated = validate_update_manifest(
        manifest,
        require_signatures=True,
        expected_channel=args.channel,
    )
    verified = verified_signature_key_ids(validated, keyring)
    if len(verified) < args.minimum_valid_signatures:
        raise ValueError(
            f"manifest has {len(verified)} trusted signature(s); "
            f"{args.minimum_valid_signatures} required"
        )
    print(
        f"verified {validated['channel']} manifest {validated['version']} "
        f"with key(s): {', '.join(verified)}"
    )
    return 0


def _command_public_key(args: argparse.Namespace) -> int:
    print(public_key_base64(_private_key(args)))
    return 0


def _add_private_key_source(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--private-key-env",
        default=DEFAULT_PRIVATE_KEY_ENV,
        metavar="NAME",
        help=f"environment variable containing the base64 key (default: {DEFAULT_PRIVATE_KEY_ENV})",
    )
    group.add_argument(
        "--private-key-file",
        type=Path,
        help="file containing the base64-encoded raw 32-byte Ed25519 private key",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser(
        "build", help="build and strictly validate unsigned release metadata"
    )
    build.add_argument("--version", required=True)
    build.add_argument("--channel", required=True, choices=("stable", "beta"))
    build.add_argument("--image-digest", required=True)
    build.add_argument("--amd64-digest", required=True)
    build.add_argument("--arm64-digest", required=True)
    build.add_argument("--source-commit", required=True)
    build.add_argument("--published-at", required=True)
    build.add_argument("--sequence", required=True, type=int)
    build.add_argument("--policy", required=True, type=Path)
    build.add_argument("--release-notes", default="")
    build.add_argument("--critical", action="store_true")
    build.add_argument("--output", required=True, type=Path)
    build.set_defaults(handler=_command_build)

    sign = subparsers.add_parser("sign", help="validate and sign a manifest")
    sign.add_argument("--input", required=True, type=Path)
    sign.add_argument("--output", required=True, type=Path)
    sign.add_argument("--key-id", required=True)
    _add_private_key_source(sign)
    sign.set_defaults(handler=_command_sign)

    verify = subparsers.add_parser(
        "verify", help="validate and verify a published manifest"
    )
    verify.add_argument("--input", required=True, type=Path)
    verify.add_argument("--keyring", required=True, type=Path)
    verify.add_argument("--channel", choices=("stable", "beta"))
    verify.add_argument("--minimum-valid-signatures", type=int, default=1)
    verify.set_defaults(handler=_command_verify)

    public_key = subparsers.add_parser(
        "public-key", help="derive a provisionable public key"
    )
    _add_private_key_source(public_key)
    public_key.set_defaults(handler=_command_public_key)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if getattr(args, "minimum_valid_signatures", 1) < 1:
            raise ValueError("minimum-valid-signatures must be at least 1")
        return int(args.handler(args))
    except (TypeError, ValueError) as exc:
        print(f"release manifest error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
