"""Strict, signed metadata for DitakNet update channels.

Schema v2 is intentionally separate from the legacy, unsigned update feed.  A
published v2 document identifies one immutable multi-platform OCI image and is
authenticated with an Ed25519 signature.  Private signing material is never
needed by the application: installations only receive channel-scoped public
keys.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import UTC, datetime
from typing import Any, Mapping
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


SCHEMA_VERSION = 2
MANIFEST_DOMAIN = b"DitakNet update manifest v2\0"
IMAGE_REPOSITORY = "ghcr.io/ditaknet-sudo/ditaknet"
SUPPORTED_CHANNELS = frozenset({"stable", "beta"})
REQUIRED_PLATFORMS = frozenset({"linux/amd64", "linux/arm64"})
ROLLBACK_POLICIES = frozenset({"state_restore_required", "unsupported"})

_SEMVER_RE = re.compile(
    r"(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    r"(?:-(?P<prerelease>"
    r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*"
    r"))?"
)
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_KEY_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "channel",
        "version",
        "docker_image",
        "image_digest",
        "docker_digest",  # accepted input alias; normalized to image_digest
        "platform_digests",
        "release_url",
        "source_commit",
        "published_at",
        "sequence",
        "compatibility",
        "critical",
        "changelog_url",
        "release_notes",
        "message",
        "upgrade_hint",
        "signatures",
    }
)
_REQUIRED_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "channel",
        "version",
        "docker_image",
        "platform_digests",
        "release_url",
        "source_commit",
        "published_at",
        "sequence",
        "compatibility",
    }
)
_COMPATIBILITY_FIELDS = frozenset(
    {
        "minimum_current_version",
        "maximum_current_version",
        "requires_backup",
        "allow_major_upgrade",
        "target_schema_revision",
        "backup_format_version",
        "rollback_policy",
    }
)
_SIGNATURE_FIELDS = frozenset({"key_id", "algorithm", "value"})


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _strict_fields(
    value: Mapping[str, Any],
    *,
    allowed: frozenset[str],
    required: frozenset[str],
    label: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{label} contains unknown field(s): {', '.join(unknown)}")
    missing = sorted(required - set(value))
    if missing:
        raise ValueError(f"{label} is missing required field(s): {', '.join(missing)}")


def _text(value: Any, label: str, *, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be a non-empty, trimmed string")
    if len(value) > maximum:
        raise ValueError(f"{label} is too long")
    return value


def _semver(value: Any, label: str) -> tuple[str, tuple[int, int, int], bool]:
    version = _text(value, label, maximum=128)
    match = _SEMVER_RE.fullmatch(version)
    if match is None:
        raise ValueError(f"{label} must be strict SemVer without build metadata")
    core = (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )
    return version, core, match.group("prerelease") is not None


def _semver_sort_key(value: str) -> tuple[Any, ...]:
    match = _SEMVER_RE.fullmatch(value)
    if match is None:  # pragma: no cover - callers validate first
        raise ValueError("invalid semantic version")
    core: tuple[Any, ...] = (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )
    prerelease = match.group("prerelease")
    if prerelease is None:
        return (*core, 1, ())
    parts: list[tuple[int, int | str]] = []
    for part in prerelease.split("."):
        parts.append((0, int(part)) if part.isdigit() else (1, part))
    return (*core, 0, tuple(parts))


def _digest(value: Any, label: str) -> str:
    digest = _text(value, label, maximum=71).lower()
    if _DIGEST_RE.fullmatch(digest) is None:
        raise ValueError(
            f"{label} must be sha256 followed by exactly 64 hex characters"
        )
    return digest


def _https_url(value: Any, label: str) -> str:
    url = _text(value, label, maximum=2048)
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError(
            f"{label} must be an HTTPS URL without credentials or fragment"
        )
    return url


def _published_at(value: Any) -> str:
    timestamp = _text(value, "published_at", maximum=64)
    if not (timestamp.endswith("Z") or timestamp.endswith("+00:00")):
        raise ValueError("published_at must be an RFC 3339 UTC timestamp")
    try:
        iso_timestamp = (
            timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
        )
        parsed = datetime.fromisoformat(iso_timestamp)
    except ValueError as exc:
        raise ValueError("published_at must be an RFC 3339 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("published_at must be an RFC 3339 UTC timestamp")
    return parsed.isoformat().replace("+00:00", "Z")


def _positive_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be an integer greater than or equal to 1")
    return value


def _localized_text(value: Any, label: str) -> dict[str, str]:
    mapping = _object(value, label)
    if not mapping:
        raise ValueError(f"{label} must not be empty")
    result: dict[str, str] = {}
    for locale, text in mapping.items():
        if (
            not isinstance(locale, str)
            or re.fullmatch(r"[a-z]{2}(?:-[A-Z]{2})?", locale) is None
        ):
            raise ValueError(f"{label} contains an invalid locale")
        result[locale] = _text(text, f"{label}.{locale}", maximum=4096)
    return result


def _decode_base64(value: Any, label: str, *, length: int) -> bytes:
    encoded = _text(value, label, maximum=256)
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"{label} must be canonical base64") from exc
    if len(decoded) != length or base64.b64encode(decoded).decode("ascii") != encoded:
        raise ValueError(f"{label} must encode exactly {length} bytes")
    return decoded


def _normalize_signatures(value: Any, *, required: bool) -> list[dict[str, str]]:
    if value is None:
        if required:
            raise ValueError("signatures is required for a published manifest")
        return []
    if not isinstance(value, list) or not value:
        raise ValueError("signatures must be a non-empty JSON array")

    signatures: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        signature = _object(item, f"signatures[{index}]")
        _strict_fields(
            signature,
            allowed=_SIGNATURE_FIELDS,
            required=_SIGNATURE_FIELDS,
            label=f"signatures[{index}]",
        )
        key_id = _text(signature["key_id"], f"signatures[{index}].key_id", maximum=64)
        if _KEY_ID_RE.fullmatch(key_id) is None:
            raise ValueError(f"signatures[{index}].key_id is invalid")
        if key_id in seen:
            raise ValueError(f"duplicate signature key_id: {key_id}")
        seen.add(key_id)
        if signature["algorithm"] != "ed25519":
            raise ValueError(f"signatures[{index}].algorithm must be ed25519")
        _decode_base64(signature["value"], f"signatures[{index}].value", length=64)
        signatures.append(
            {"key_id": key_id, "algorithm": "ed25519", "value": signature["value"]}
        )
    return sorted(signatures, key=lambda item: item["key_id"])


def validate_update_manifest(
    data: Any,
    *,
    require_signatures: bool = True,
    expected_channel: str | None = None,
) -> dict[str, Any]:
    """Validate and normalize one schema-v2 update manifest.

    Unknown fields and permissive aliases are rejected, except for the
    documented ``docker_digest`` alias.  Published manifests require at least
    one structurally valid signature; cryptographic trust is checked separately
    with :func:`verify_manifest_signatures`.
    """

    manifest = _object(data, "manifest")
    _strict_fields(
        manifest,
        allowed=_TOP_LEVEL_FIELDS,
        required=_REQUIRED_TOP_LEVEL_FIELDS,
        label="manifest",
    )
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")

    channel = _text(manifest["channel"], "channel", maximum=16).lower()
    if channel not in SUPPORTED_CHANNELS:
        raise ValueError("channel must be stable or beta")
    if expected_channel is not None:
        expected = str(expected_channel).strip().lower()
        if expected not in SUPPORTED_CHANNELS:
            raise ValueError("expected_channel must be stable or beta")
        if channel != expected:
            raise ValueError(
                f"manifest channel {channel!r} does not match {expected!r}"
            )

    version, version_core, prerelease = _semver(manifest["version"], "version")
    if channel == "stable" and prerelease:
        raise ValueError("stable channel manifests cannot publish a prerelease")

    docker_image = _text(manifest["docker_image"], "docker_image", maximum=256)
    expected_image = f"{IMAGE_REPOSITORY}:{version}"
    if docker_image != expected_image:
        raise ValueError(f"docker_image must be exactly {expected_image}")

    canonical_digest = manifest.get("image_digest")
    alias_digest = manifest.get("docker_digest")
    if canonical_digest is None and alias_digest is None:
        raise ValueError("manifest is missing required field: image_digest")
    if canonical_digest is not None and alias_digest is not None:
        normalized_canonical = _digest(canonical_digest, "image_digest")
        normalized_alias = _digest(alias_digest, "docker_digest")
        if normalized_canonical != normalized_alias:
            raise ValueError("image_digest and docker_digest disagree")
        image_digest = normalized_canonical
    else:
        image_digest = _digest(
            canonical_digest if canonical_digest is not None else alias_digest,
            "image_digest" if canonical_digest is not None else "docker_digest",
        )

    platform_digests_raw = _object(manifest["platform_digests"], "platform_digests")
    actual_platforms = set(platform_digests_raw)
    if actual_platforms != REQUIRED_PLATFORMS:
        missing = sorted(REQUIRED_PLATFORMS - actual_platforms)
        unknown = sorted(actual_platforms - REQUIRED_PLATFORMS)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unknown:
            details.append(f"unknown {', '.join(unknown)}")
        raise ValueError(
            f"platform_digests must contain exactly the supported platforms ({'; '.join(details)})"
        )
    platform_digests = {
        platform: _digest(
            platform_digests_raw[platform], f"platform_digests.{platform}"
        )
        for platform in sorted(REQUIRED_PLATFORMS)
    }

    release_url = _https_url(manifest["release_url"], "release_url")
    expected_release_url = (
        f"https://github.com/ditaknet-sudo/ditaknet/releases/tag/v{version}"
    )
    if release_url != expected_release_url:
        raise ValueError(f"release_url must be exactly {expected_release_url}")

    source_commit = _text(
        manifest["source_commit"], "source_commit", maximum=40
    ).lower()
    if _COMMIT_RE.fullmatch(source_commit) is None:
        raise ValueError("source_commit must be a full 40-character Git commit hash")

    sequence = _positive_integer(manifest["sequence"], "sequence")
    compatibility_raw = _object(manifest["compatibility"], "compatibility")
    _strict_fields(
        compatibility_raw,
        allowed=_COMPATIBILITY_FIELDS,
        required=_COMPATIBILITY_FIELDS,
        label="compatibility",
    )
    minimum, minimum_core, _ = _semver(
        compatibility_raw["minimum_current_version"],
        "compatibility.minimum_current_version",
    )
    maximum, _, _ = _semver(
        compatibility_raw["maximum_current_version"],
        "compatibility.maximum_current_version",
    )
    if _semver_sort_key(minimum) > _semver_sort_key(maximum):
        raise ValueError(
            "compatibility minimum_current_version exceeds maximum_current_version"
        )
    if _semver_sort_key(maximum) > _semver_sort_key(version):
        raise ValueError("compatibility maximum_current_version exceeds target version")
    requires_backup = compatibility_raw["requires_backup"]
    if requires_backup is not True:
        raise ValueError("compatibility.requires_backup must be true")
    allow_major_upgrade = compatibility_raw["allow_major_upgrade"]
    if not isinstance(allow_major_upgrade, bool):
        raise ValueError("compatibility.allow_major_upgrade must be a boolean")
    if not allow_major_upgrade and minimum_core[0] != version_core[0]:
        raise ValueError(
            "compatibility range crosses a major version but allow_major_upgrade is false"
        )
    rollback_policy = _text(
        compatibility_raw["rollback_policy"],
        "compatibility.rollback_policy",
        maximum=32,
    )
    if rollback_policy not in ROLLBACK_POLICIES:
        raise ValueError(
            "compatibility.rollback_policy must be state_restore_required or unsupported"
        )
    compatibility = {
        "minimum_current_version": minimum,
        "maximum_current_version": maximum,
        "requires_backup": True,
        "allow_major_upgrade": allow_major_upgrade,
        "target_schema_revision": _positive_integer(
            compatibility_raw["target_schema_revision"],
            "compatibility.target_schema_revision",
        ),
        "backup_format_version": _positive_integer(
            compatibility_raw["backup_format_version"],
            "compatibility.backup_format_version",
        ),
        "rollback_policy": rollback_policy,
    }

    normalized: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "channel": channel,
        "version": version,
        "docker_image": docker_image,
        "image_digest": image_digest,
        "platform_digests": platform_digests,
        "release_url": release_url,
        "source_commit": source_commit,
        "published_at": _published_at(manifest["published_at"]),
        "sequence": sequence,
        "compatibility": compatibility,
    }

    if "critical" in manifest:
        if not isinstance(manifest["critical"], bool):
            raise ValueError("critical must be a boolean")
        normalized["critical"] = manifest["critical"]
    if "changelog_url" in manifest:
        normalized["changelog_url"] = _https_url(
            manifest["changelog_url"], "changelog_url"
        )
    if "release_notes" in manifest:
        normalized["release_notes"] = _text(
            manifest["release_notes"], "release_notes", maximum=65536
        )
    for key in ("message", "upgrade_hint"):
        if key in manifest:
            normalized[key] = _localized_text(manifest[key], key)

    signatures = _normalize_signatures(
        manifest.get("signatures"), required=require_signatures
    )
    if signatures:
        normalized["signatures"] = signatures
    return normalized


def canonical_manifest_payload(data: Any) -> bytes:
    """Return domain-separated canonical JSON bytes, excluding signatures."""

    normalized = validate_update_manifest(data, require_signatures=False)
    normalized.pop("signatures", None)
    canonical_json = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return MANIFEST_DOMAIN + canonical_json


def _private_key(value: bytes | str) -> Ed25519PrivateKey:
    encoded = value.decode("ascii") if isinstance(value, bytes) else value
    raw = _decode_base64(encoded.strip(), "private key", length=32)
    return Ed25519PrivateKey.from_private_bytes(raw)


def sign_manifest(
    data: Any,
    *,
    key_id: str,
    private_key: bytes | str,
) -> dict[str, Any]:
    """Add or rotate one Ed25519 signature without exposing private material."""

    if _KEY_ID_RE.fullmatch(str(key_id)) is None:
        raise ValueError("key_id is invalid")
    normalized = validate_update_manifest(data, require_signatures=False)
    signature = _private_key(private_key).sign(canonical_manifest_payload(normalized))
    entry = {
        "key_id": key_id,
        "algorithm": "ed25519",
        "value": base64.b64encode(signature).decode("ascii"),
    }
    existing = {item["key_id"]: item for item in normalized.get("signatures", [])}
    existing[key_id] = entry
    normalized["signatures"] = [existing[item] for item in sorted(existing)]
    return validate_update_manifest(normalized, require_signatures=True)


def _channel_keys(
    keyring: Mapping[str, Mapping[str, bytes | str]], channel: str
) -> Mapping[str, bytes | str]:
    keys = keyring.get(channel)
    if not isinstance(keys, Mapping):
        raise ValueError(f"no public keyring configured for channel {channel!r}")
    return keys


def verified_signature_key_ids(
    data: Any,
    keyring: Mapping[str, Mapping[str, bytes | str]],
) -> tuple[str, ...]:
    """Return trusted channel key IDs whose signatures verify."""

    manifest = validate_update_manifest(data, require_signatures=True)
    trusted_keys = _channel_keys(keyring, manifest["channel"])
    payload = canonical_manifest_payload(manifest)
    verified: list[str] = []
    for signature in manifest["signatures"]:
        public_value = trusted_keys.get(signature["key_id"])
        if public_value is None:
            continue
        encoded = (
            public_value.decode("ascii")
            if isinstance(public_value, bytes)
            else public_value
        )
        try:
            public_raw = _decode_base64(
                encoded.strip(),
                f"public key {signature['key_id']}",
                length=32,
            )
            signature_raw = _decode_base64(
                signature["value"],
                f"signature {signature['key_id']}",
                length=64,
            )
            Ed25519PublicKey.from_public_bytes(public_raw).verify(
                signature_raw, payload
            )
        except (InvalidSignature, ValueError):
            continue
        verified.append(signature["key_id"])
    return tuple(sorted(verified))


def verify_manifest_signatures(
    data: Any,
    keyring: Mapping[str, Mapping[str, bytes | str]],
    *,
    minimum_valid_signatures: int = 1,
) -> bool:
    """Verify a signed manifest against only its channel's trusted keyring."""

    minimum = _positive_integer(minimum_valid_signatures, "minimum_valid_signatures")
    return len(verified_signature_key_ids(data, keyring)) >= minimum


def public_key_base64(private_key: bytes | str) -> str:
    """Derive the raw Ed25519 public key for an offline provisioning step."""

    from cryptography.hazmat.primitives import serialization

    raw = (
        _private_key(private_key)
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    return base64.b64encode(raw).decode("ascii")


# Short alias for callers that already use ``validate_manifest`` terminology.
validate_manifest_v2 = validate_update_manifest
