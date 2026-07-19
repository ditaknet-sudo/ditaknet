"""
DitakNet update checker — notify-only, never auto-applies updates.

Design goals:
  - Self-hosted friendly (no telemetry: no hostname, IP, license, or user data)
  - Non-blocking: failures never crash the app or affect /health / monitoring
  - Manual upgrades only (Docker/TrueNAS pin exact version tags)
  - Manifest over HTTPS with ETag caching + GitHub Releases API fallback
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import random
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from loguru import logger

from ditaknet.config import settings

# ── Persistence keys (app_settings) ──────────────────────────────
_KEY_LAST_CHECKED = "update_last_checked_at"
_KEY_LAST_SUCCESS = "update_last_success_at"
_KEY_ETAG = "update_manifest_etag"
_KEY_PAYLOAD = "update_last_payload_json"
_KEY_FAILURES = "update_consecutive_failures"
_KEY_BACKOFF_UNTIL = "update_backoff_until"
_KEY_DISMISSED = "update_dismissed_version"
_KEY_SNOOZE_UNTIL = "update_snooze_until"
_KEY_ENABLED_OVERRIDE = "update_check_enabled_override"  # "", "true", "false"

_DEFAULT_MANIFEST_URL = (
    "https://raw.githubusercontent.com/ditaknet-sudo/ditaknet/main/update-manifest.json"
)
_USER_AGENT = "DitakNet-UpdateChecker/1.0"
_MIN_TIMEOUT = 5.0
_MAX_TIMEOUT = 10.0
_MAX_BACKOFF_SECONDS = 24 * 3600
_SNOOZE_HOURS = 24

_CACHE: dict[str, Any] = {"expires_at": 0.0, "payload": None}
_CACHE_SECONDS = 60
_CHECKER_TASK: asyncio.Task | None = None
_CHECKER_STOP = asyncio.Event()


# ── Semantic versioning ─────────────────────────────────────────


def _clean_version(value: str | None) -> str:
    raw = str(value or "").strip()
    if raw.lower().startswith("version "):
        raw = raw[8:].strip()
    if raw.lower().startswith("v"):
        raw = raw[1:].strip()
    # Drop build metadata (+...)
    if "+" in raw:
        raw = raw.split("+", 1)[0]
    return raw.strip()


def parse_semver(value: str | None) -> tuple[int, int, int, tuple[str, ...]] | None:
    """Parse SemVer core + optional pre-release. Returns None if invalid/empty."""
    cleaned = _clean_version(value)
    if not cleaned:
        return None
    match = re.fullmatch(
        r"(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?",
        cleaned,
    )
    if not match:
        # Allow shorter forms like 2.0
        match = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?", cleaned)
        if not match:
            return None
        major, minor, patch, pre = match.group(1), match.group(2), match.group(3) or "0", match.group(4)
    else:
        major, minor, patch, pre = match.group(1), match.group(2), match.group(3), match.group(4)
    pre_parts: tuple[str, ...] = tuple(pre.split(".")) if pre else ()
    return int(major), int(minor), int(patch), pre_parts


def _pre_key(parts: tuple[str, ...]) -> tuple:
    """SemVer: no pre-release > any pre-release; numeric segments compare as ints."""
    if not parts:
        return ((1, 0, 0),)  # release beats pre-release
    out: list[tuple] = []
    for part in parts:
        if part.isdigit():
            # Numeric identifiers have lower precedence than non-numeric ones.
            # The discriminator also keeps tuple element types comparable.
            out.append((0, 0, int(part)))
        else:
            out.append((0, 1, part.lower()))
    return tuple(out)


def compare_versions(a: str | None, b: str | None) -> int:
    """Return -1 if a<b, 0 if equal, 1 if a>b. Invalid versions compare as equal (0)."""
    pa, pb = parse_semver(a), parse_semver(b)
    if pa is None or pb is None:
        return 0
    a_core, b_core = pa[:3], pb[:3]
    if a_core < b_core:
        return -1
    if a_core > b_core:
        return 1
    ka, kb = _pre_key(pa[3]), _pre_key(pb[3])
    if ka < kb:
        return -1
    if ka > kb:
        return 1
    return 0


def is_newer_version(latest: str | None, current: str | None) -> bool:
    """True when latest is a valid SemVer strictly newer than current."""
    if parse_semver(latest) is None or parse_semver(current) is None:
        return False
    return compare_versions(latest, current) > 0


# ── Config helpers ───────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _timeout_seconds() -> float:
    raw = float(getattr(settings, "app_update_check_timeout_seconds", 8.0) or 8.0)
    return max(_MIN_TIMEOUT, min(_MAX_TIMEOUT, raw))


def _interval_hours() -> float:
    hours = float(getattr(settings, "app_update_check_interval_hours", 6.0) or 6.0)
    return max(1.0, min(168.0, hours))


def _channel() -> str:
    channel = str(getattr(settings, "app_update_channel", "stable") or "stable").strip().lower()
    return channel or "stable"


def _manifest_url() -> str:
    url = (
        str(getattr(settings, "app_update_manifest_url", "") or "").strip()
        or str(getattr(settings, "app_update_check_url", "") or "").strip()
        or _DEFAULT_MANIFEST_URL
    )
    return url


def _github_repo() -> str:
    repo = str(settings.github_repository or "").strip()
    if repo:
        return repo.removeprefix("https://github.com/").strip("/")
    return "ditaknet-sudo/ditaknet"


def _github_releases_url() -> str:
    return f"https://api.github.com/repos/{_github_repo()}/releases/latest"


async def _is_check_enabled() -> bool:
    from ditaknet import database as db

    override = (await db.get_app_setting(_KEY_ENABLED_OVERRIDE, "") or "").strip().lower()
    if override in {"0", "false", "no", "off"}:
        return False
    if override in {"1", "true", "yes", "on"}:
        return True
    return bool(settings.app_update_check_enabled)


async def is_update_check_enabled() -> bool:
    return await _is_check_enabled()


async def set_check_enabled(enabled: bool) -> None:
    from ditaknet import database as db

    await db.set_app_setting(_KEY_ENABLED_OVERRIDE, "true" if enabled else "false")
    _CACHE["payload"] = None
    _CACHE["expires_at"] = 0.0


# ── Manifest schema ──────────────────────────────────────────────


def validate_manifest(data: Any) -> dict[str, Any]:
    """Validate and normalize a public update manifest. Raises ValueError on bad input."""
    if not isinstance(data, dict):
        raise ValueError("Manifest must be a JSON object")

    latest = _clean_version(
        str(
            data.get("latest_version")
            or data.get("version")
            or data.get("tag_name")
            or ""
        )
    )
    if parse_semver(latest) is None:
        raise ValueError("Manifest latest_version is missing or invalid")

    minimum = _clean_version(str(data.get("minimum_supported_version") or ""))
    if minimum and parse_semver(minimum) is None:
        raise ValueError("Manifest minimum_supported_version is invalid")

    channel = str(data.get("channel") or "stable").strip().lower() or "stable"
    release_url = str(
        data.get("release_url") or data.get("html_url") or data.get("url") or ""
    ).strip()
    docker_image = str(
        data.get("docker_image") or data.get("image") or data.get("latest_image_tag") or ""
    ).strip()
    if docker_image and ":" not in docker_image and not docker_image.startswith("sha256:"):
        docker_image = f"ghcr.io/ditaknet-sudo/ditaknet:{docker_image}"
    changelog_url = str(data.get("changelog_url") or release_url or "").strip()
    critical = bool(data.get("critical") is True)
    release_date = str(data.get("release_date") or "").strip()

    message_raw = data.get("message")
    message: dict[str, str] = {}
    if isinstance(message_raw, dict):
        for key, value in message_raw.items():
            text = str(value or "").strip()
            if text:
                message[str(key)] = text
    elif isinstance(message_raw, str) and message_raw.strip():
        message = {"en": message_raw.strip()}

    upgrade_hint_raw = data.get("upgrade_hint")
    upgrade_hint: dict[str, str] = {}
    if isinstance(upgrade_hint_raw, dict):
        for key, value in upgrade_hint_raw.items():
            text = str(value or "").strip()
            if text:
                upgrade_hint[str(key)] = text
    elif isinstance(upgrade_hint_raw, str) and upgrade_hint_raw.strip():
        upgrade_hint = {"en": upgrade_hint_raw.strip()}

    checksums = data.get("checksums")
    if checksums is not None and not isinstance(checksums, dict):
        raise ValueError("Manifest checksums must be an object")
    if isinstance(checksums, dict):
        for key, value in checksums.items():
            text = str(value or "").strip().lower()
            if text.startswith("sha256:"):
                text = text[7:]
            # Allow non-hex hints; only validate pure hex digests
            if re.fullmatch(r"[0-9a-f]{64}", text) is None and " " not in str(value):
                if re.fullmatch(r"[0-9a-f]+", text) and len(text) != 64:
                    raise ValueError(f"Invalid SHA-256 checksum for {key}")

    signature = str(data.get("signature") or "").strip()
    release_notes = str(
        data.get("release_notes") or data.get("release_notes_text") or data.get("body") or ""
    ).strip()

    return {
        "channel": channel,
        "latest_version": latest,
        "minimum_supported_version": minimum or None,
        "release_date": release_date or None,
        "release_url": release_url or None,
        "docker_image": docker_image or None,
        "changelog_url": changelog_url or None,
        "critical": critical,
        "message": message,
        "upgrade_hint": upgrade_hint,
        "checksums": checksums if isinstance(checksums, dict) else {},
        "signature": signature or None,
        "release_notes": release_notes or None,
    }


def canonical_manifest_payload(data: Any) -> bytes:
    """Return deterministic manifest bytes with the embedded signature omitted.

    Release tooling can use this function's JSON format when producing the
    HMAC. Omitting ``signature`` avoids a self-referential payload while sorted
    compact JSON makes verification independent of whitespace and key order.
    """
    parsed: Any = data
    if isinstance(data, (bytes, bytearray)):
        parsed = json.loads(bytes(data).decode("utf-8"))
    elif isinstance(data, str):
        parsed = json.loads(data)
    if not isinstance(parsed, dict):
        raise ValueError("Manifest signing payload must be a JSON object")

    unsigned = dict(parsed)
    unsigned.pop("signature", None)
    return json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def verify_manifest_signature(raw_body: bytes, signature: str | None) -> bool:
    """
    Optional HMAC-SHA256 verification.

    When DITAKNET_UPDATE_MANIFEST_SIGNING_KEY is unset, verification is skipped (True).
    Signature may be hex or base64 of HMAC over canonical JSON with the
    embedded ``signature`` field omitted.
    """
    key = str(getattr(settings, "app_update_manifest_signing_key", "") or "").strip()
    if not key:
        return True
    if not signature:
        return False

    encoded_signature = signature.strip()
    candidate = encoded_signature.lower()
    if candidate.startswith("sha256="):
        encoded_signature = encoded_signature[7:].strip()
        candidate = encoded_signature.lower()
    try:
        import base64

        candidate_hex = candidate
        if not re.fullmatch(r"[0-9a-f]{64}", candidate):
            decoded = base64.b64decode(encoded_signature, validate=True)
            if len(decoded) != hashlib.sha256().digest_size:
                return False
            candidate_hex = decoded.hex()
    except Exception:
        return False

    try:
        payload = canonical_manifest_payload(raw_body)
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return False

    return hmac.compare_digest(
        hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest(),
        candidate_hex,
    )


def verify_file_sha256(content: bytes, expected_hex: str) -> bool:
    """Return True when content SHA-256 matches expected hex digest."""
    digest = str(expected_hex or "").strip().lower().removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        return False
    actual = hashlib.sha256(content).hexdigest()
    return hmac.compare_digest(actual, digest)


# ── Localized message ────────────────────────────────────────────


def _pick_localized(mapping: dict[str, str] | None, lang: str = "en") -> str:
    if not mapping:
        return ""
    for key in (lang, lang.split("-")[0], "en", "hy", "ru"):
        if key in mapping and mapping[key]:
            return mapping[key]
    if mapping:
        return next(iter(mapping.values()))
    return ""


# ── HTTP fetch ───────────────────────────────────────────────────


def _safe_headers(etag: str | None = None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": _USER_AGENT,
    }
    # Intentionally no hostname / license / identity headers
    if etag:
        headers["If-None-Match"] = etag
    return headers


async def _fetch_json(
    url: str,
    *,
    etag: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> tuple[int, dict[str, Any] | None, str | None, bytes]:
    """Return (status, json|None, etag|None, raw_body)."""
    owns = client is None
    if owns:
        client = httpx.AsyncClient(timeout=_timeout_seconds(), follow_redirects=True)
    assert client is not None
    try:
        if not url.lower().startswith("https://"):
            raise ValueError("Update checks require HTTPS")
        response = await client.get(url, headers=_safe_headers(etag))
        new_etag = response.headers.get("ETag")
        body = response.content or b""
        if response.status_code == 304:
            return 304, None, new_etag or etag, body
        if response.status_code == 403 and "rate limit" in response.text.lower():
            raise RuntimeError("GitHub rate limit")
        if response.status_code >= 500:
            raise RuntimeError(f"HTTP {response.status_code}")
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Response JSON must be an object")
        return response.status_code, data, new_etag, body
    finally:
        if owns:
            await client.aclose()


def _payload_from_github_release(data: dict[str, Any]) -> dict[str, Any]:
    tag = _clean_version(str(data.get("tag_name") or data.get("name") or ""))
    if parse_semver(tag) is None:
        raise ValueError("GitHub release tag is not a valid SemVer")
    image = f"ghcr.io/ditaknet-sudo/ditaknet:{tag}"
    return validate_manifest(
        {
            "channel": "stable",
            "latest_version": tag,
            "release_date": str(data.get("published_at") or "")[:10],
            "release_url": str(data.get("html_url") or ""),
            "docker_image": image,
            "changelog_url": str(data.get("html_url") or ""),
            "critical": False,
            "message": {"en": f"DitakNet {tag} is available"},
            "release_notes": str(data.get("body") or ""),
        }
    )


async def _load_state() -> dict[str, Any]:
    from ditaknet import database as db

    payload_raw = await db.get_app_setting(_KEY_PAYLOAD, "") or ""
    payload: dict[str, Any] | None = None
    if payload_raw:
        try:
            parsed = json.loads(payload_raw)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = None
    failures_raw = await db.get_app_setting(_KEY_FAILURES, "0") or "0"
    try:
        failures = int(failures_raw)
    except ValueError:
        failures = 0
    return {
        "last_checked_at": await db.get_app_setting(_KEY_LAST_CHECKED, "") or None,
        "last_success_at": await db.get_app_setting(_KEY_LAST_SUCCESS, "") or None,
        "etag": await db.get_app_setting(_KEY_ETAG, "") or None,
        "payload": payload,
        "failures": max(0, failures),
        "backoff_until": await db.get_app_setting(_KEY_BACKOFF_UNTIL, "") or None,
        "dismissed_version": await db.get_app_setting(_KEY_DISMISSED, "") or None,
        "snooze_until": await db.get_app_setting(_KEY_SNOOZE_UNTIL, "") or None,
    }


async def _save_success(manifest: dict[str, Any], etag: str | None) -> None:
    from ditaknet import database as db

    now = _now_iso()
    await db.set_app_setting(_KEY_LAST_CHECKED, now)
    await db.set_app_setting(_KEY_LAST_SUCCESS, now)
    await db.set_app_setting(_KEY_FAILURES, "0")
    await db.set_app_setting(_KEY_BACKOFF_UNTIL, "")
    if etag:
        await db.set_app_setting(_KEY_ETAG, etag)
    await db.set_app_setting(_KEY_PAYLOAD, json.dumps(manifest, ensure_ascii=False))


async def _save_failure(message: str) -> None:
    from ditaknet import database as db

    state = await _load_state()
    failures = int(state["failures"]) + 1
    # Exponential backoff: 5m, 10m, 20m … capped at 24h, plus jitter
    base = min(_MAX_BACKOFF_SECONDS, 300 * (2 ** min(failures - 1, 8)))
    jitter = random.uniform(0, min(120.0, base * 0.2))
    until = datetime.now(UTC) + timedelta(seconds=base + jitter)
    await db.set_app_setting(_KEY_LAST_CHECKED, _now_iso())
    await db.set_app_setting(_KEY_FAILURES, str(failures))
    await db.set_app_setting(_KEY_BACKOFF_UNTIL, until.isoformat())
    logger.debug("Update check failed (#{}) — backoff until {}: {}", failures, until.isoformat(), message)


def _in_backoff(state: dict[str, Any]) -> bool:
    raw = state.get("backoff_until")
    if not raw:
        return False
    try:
        until = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return datetime.now(UTC) < until
    except Exception:
        return False


def _banner_visibility(
    *,
    update_available: bool,
    latest: str | None,
    critical: bool,
    dismissed: str | None,
    snooze_until: str | None,
) -> dict[str, Any]:
    if not update_available or not latest:
        return {"show_banner": False, "can_dismiss": True, "snoozed": False}

    snoozed = False
    if snooze_until:
        try:
            until = datetime.fromisoformat(str(snooze_until).replace("Z", "+00:00"))
            snoozed = datetime.now(UTC) < until
        except Exception:
            snoozed = False

    dismissed_match = bool(dismissed and compare_versions(dismissed, latest) >= 0)
    can_dismiss = not critical
    if critical and dismissed_match:
        # Critical updates cannot be permanently hidden
        dismissed_match = False

    show = not snoozed and not dismissed_match
    return {
        "show_banner": show,
        "can_dismiss": can_dismiss,
        "snoozed": snoozed,
        "dismissed": dismissed_match,
    }


def enrich_update_status(payload: dict[str, Any], *, lang: str = "en") -> dict[str, Any]:
    """Public API shape for Settings / Dashboard / notifications."""
    current = settings.app_version
    latest = payload.get("latest_version")
    update_available = bool(payload.get("update_available"))
    critical = bool(payload.get("critical"))
    docker_image = payload.get("docker_image") or payload.get("ghcr_image")
    if not docker_image and latest:
        docker_image = f"ghcr.io/ditaknet-sudo/ditaknet:{_clean_version(str(latest))}"

    message_map = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    hint_map = payload.get("upgrade_hint") if isinstance(payload.get("upgrade_hint"), dict) else {}
    localized_message = _pick_localized(message_map, lang) or payload.get("manifest_message") or ""
    localized_hint = _pick_localized(hint_map, lang)

    visibility = _banner_visibility(
        update_available=update_available,
        latest=str(latest) if latest else None,
        critical=critical,
        dismissed=payload.get("dismissed_version"),
        snooze_until=payload.get("snooze_until"),
    )

    error = payload.get("error")
    if payload.get("source") == "error" and not error:
        error = payload.get("message") or "Could not check updates"

    checked = payload.get("last_checked_at") or payload.get("checked_at")
    return {
        **payload,
        "current_version": current,
        "current_image_tag": settings.image_tag.strip() or None,
        "latest_version": latest,
        "update_available": update_available,
        "critical": critical,
        "last_checked_at": checked,
        "checked_at": checked,
        "last_checked": checked,
        "last_success_at": payload.get("last_success_at"),
        "release_url": payload.get("release_url") or settings.app_update_release_url.strip() or None,
        "changelog_url": payload.get("changelog_url") or payload.get("release_url"),
        "release_notes_url": payload.get("changelog_url") or payload.get("release_url"),
        "release_notes_text": payload.get("release_notes") or payload.get("release_notes_text"),
        "docker_image": docker_image,
        "ghcr_image": docker_image,
        "update_channel": payload.get("channel") or _channel(),
        "channel": payload.get("channel") or _channel(),
        "localized_message": localized_message,
        "upgrade_hint": localized_hint,
        "pull_command": f"docker pull {docker_image}" if docker_image else None,
        "auto_update_enabled": False,
        "backup_before_update": True,
        "show_banner": visibility["show_banner"],
        "can_dismiss": visibility["can_dismiss"],
        "snoozed": visibility["snoozed"],
        "github_repository": settings.github_repository.strip() or f"https://github.com/{_github_repo()}",
        "build_commit": settings.build_commit.strip() or None,
        "build_date": settings.release_build_date or settings.build_date.strip() or None,
        "source_configured": bool(payload.get("source_configured", True)),
        "error": error,
        "error_message": error if error else "",
        "minimum_supported_version": payload.get("minimum_supported_version"),
        "release_date": payload.get("release_date"),
        "announcements": payload.get("announcements") or [],
        "promotions": payload.get("promotions") or [],
        "manifest_message": localized_message or payload.get("manifest_message"),
        "customer_notice_available": bool(
            payload.get("customer_notice_available")
            or localized_message
            or payload.get("release_notes")
        ),
    }


def _base_payload(source: str, message: str = "", *, error: str | None = None) -> dict[str, Any]:
    return {
        "status": source,
        "source": source,
        "message": message,
        "error": error,
        "checked_at": _now_iso(),
        "last_checked_at": _now_iso(),
        "current_version": settings.app_version,
        "latest_version": None,
        "update_available": False,
        "critical": False,
        "release_url": settings.app_update_release_url.strip() or None,
        "docker_image": None,
        "channel": _channel(),
        "auto_update_enabled": False,
        "source_configured": True,
    }


def _apply_manifest(manifest: dict[str, Any], *, source: str) -> dict[str, Any]:
    latest = manifest["latest_version"]
    payload = _base_payload(source)
    payload.update(
        {
            "latest_version": latest,
            "minimum_supported_version": manifest.get("minimum_supported_version"),
            "release_date": manifest.get("release_date"),
            "release_url": manifest.get("release_url"),
            "changelog_url": manifest.get("changelog_url"),
            "docker_image": manifest.get("docker_image"),
            "critical": bool(manifest.get("critical")),
            "message": manifest.get("message") or {},
            "upgrade_hint": manifest.get("upgrade_hint") or {},
            "release_notes": manifest.get("release_notes"),
            "release_notes_text": manifest.get("release_notes"),
            "checksums": manifest.get("checksums") or {},
            "channel": manifest.get("channel") or _channel(),
            "update_available": is_newer_version(latest, settings.app_version),
            "status": "update_available"
            if is_newer_version(latest, settings.app_version)
            else "up_to_date",
        }
    )
    return payload


async def check_for_updates(*, force: bool = False, lang: str = "en") -> dict[str, Any]:
    """
    Perform (or return cached) update status.

    Never raises to callers for network/parse failures — returns error payload.
    """
    enabled = await _is_check_enabled()
    if not enabled:
        payload = _base_payload("disabled", "Update checks are disabled.")
        payload["source_configured"] = False
        return enrich_update_status(payload, lang=lang)

    state = await _load_state()
    if not force and _in_backoff(state) and state.get("payload"):
        cached = dict(state["payload"])
        cached["dismissed_version"] = state.get("dismissed_version")
        cached["snooze_until"] = state.get("snooze_until")
        cached["last_checked_at"] = state.get("last_checked_at")
        cached["last_success_at"] = state.get("last_success_at")
        return enrich_update_status(cached, lang=lang)

    now_mono = time.monotonic()
    if (
        not force
        and _CACHE["payload"] is not None
        and now_mono < float(_CACHE["expires_at"])
    ):
        cached = dict(_CACHE["payload"])
        cached["dismissed_version"] = state.get("dismissed_version")
        cached["snooze_until"] = state.get("snooze_until")
        return enrich_update_status(cached, lang=lang)

    # Env override (catalog inject) still supported
    env_latest = settings.app_latest_version.strip()
    env_tag = settings.app_latest_image_tag.strip()
    if env_latest or env_tag:
        try:
            manifest = validate_manifest(
                {
                    "latest_version": env_latest or env_tag,
                    "docker_image": env_tag
                    if ":" in env_tag
                    else (f"ghcr.io/ditaknet-sudo/ditaknet:{env_tag}" if env_tag else None),
                    "channel": _channel(),
                    "release_url": settings.app_update_release_url,
                    "critical": False,
                    "message": {"en": f"DitakNet {env_latest or env_tag} is available"},
                }
            )
            payload = _apply_manifest(manifest, source="env")
            payload["last_success_at"] = _now_iso()
            await _save_success(payload, None)
            payload["dismissed_version"] = state.get("dismissed_version")
            payload["snooze_until"] = state.get("snooze_until")
            _CACHE["payload"] = dict(payload)
            _CACHE["expires_at"] = now_mono + _CACHE_SECONDS
            return enrich_update_status(payload, lang=lang)
        except ValueError as exc:
            logger.debug("Invalid env update override: {}", exc)

    etag = state.get("etag")
    manifest_url = _manifest_url()
    error_message: str | None = None
    payload: dict[str, Any] | None = None

    try:
        async with httpx.AsyncClient(timeout=_timeout_seconds(), follow_redirects=True) as client:
            try:
                status, data, new_etag, raw = await _fetch_json(
                    manifest_url, etag=etag if not force else None, client=client
                )
                if status == 304 and state.get("payload"):
                    payload = dict(state["payload"])
                    payload["checked_at"] = _now_iso()
                    payload["last_checked_at"] = payload["checked_at"]
                    payload["status"] = "not_modified"
                    payload["source"] = "manifest"
                    await _save_success(payload, new_etag or etag)
                elif data is not None:
                    if not verify_manifest_signature(raw, str(data.get("signature") or "") or None):
                        raise ValueError("Manifest signature verification failed")
                    manifest = validate_manifest(data)
                    # Optional channel filter
                    if manifest.get("channel") and manifest["channel"] != _channel():
                        if _channel() != "stable" or manifest["channel"] not in {"stable", ""}:
                            logger.debug(
                                "Ignoring manifest channel {} (want {})",
                                manifest.get("channel"),
                                _channel(),
                            )
                    payload = _apply_manifest(manifest, source="manifest")
                    await _save_success(payload, new_etag)
            except Exception as primary_exc:
                error_message = f"{type(primary_exc).__name__}: {primary_exc}"
                if str(
                    getattr(settings, "app_update_manifest_signing_key", "") or ""
                ).strip():
                    # A configured key means signed metadata is mandatory.
                    # Never downgrade to the unsigned GitHub Releases API after
                    # a fetch or signature failure.
                    raise
                logger.debug("Manifest fetch failed, trying GitHub Releases: {}", error_message)
                # Fallback: GitHub Releases API
                status, data, new_etag, raw = await _fetch_json(
                    _github_releases_url(), etag=None, client=client
                )
                if data is None:
                    raise RuntimeError("Empty GitHub release response")
                manifest = _payload_from_github_release(data)
                payload = _apply_manifest(manifest, source="github_releases")
                await _save_success(payload, new_etag)
                error_message = None
    except Exception as exc:
        error_message = f"{type(exc).__name__}: {exc}"
        await _save_failure(error_message)
        if state.get("payload"):
            payload = dict(state["payload"])
            payload["error"] = error_message
            payload["source"] = "cached_after_error"
            payload["status"] = "error"
            payload["last_checked_at"] = _now_iso()
        else:
            payload = _base_payload("error", error_message, error=error_message)

    assert payload is not None
    payload["dismissed_version"] = state.get("dismissed_version")
    payload["snooze_until"] = state.get("snooze_until")
    payload["last_success_at"] = (
        payload.get("last_success_at") or state.get("last_success_at")
    )
    if not payload.get("last_checked_at"):
        payload["last_checked_at"] = state.get("last_checked_at") or _now_iso()

    _CACHE["payload"] = dict(payload)
    _CACHE["expires_at"] = time.monotonic() + _CACHE_SECONDS
    return enrich_update_status(payload, lang=lang)


async def get_update_status(*, force: bool = False, lang: str = "en") -> dict[str, Any]:
    """Backward-compatible entry point used across the app."""
    try:
        return await check_for_updates(force=force, lang=lang)
    except Exception as exc:
        logger.warning("Update status unexpected failure: {}", exc)
        return enrich_update_status(
            _base_payload("error", str(exc), error=str(exc)),
            lang=lang,
        )


async def snooze_update_banner(*, hours: int = _SNOOZE_HOURS) -> dict[str, Any]:
    from ditaknet import database as db

    until = datetime.now(UTC) + timedelta(hours=max(1, hours))
    await db.set_app_setting(_KEY_SNOOZE_UNTIL, until.isoformat())
    _CACHE["payload"] = None
    return await get_update_status(force=False)


async def dismiss_update_version(version: str | None = None) -> dict[str, Any]:
    """Hide a non-critical update. Critical updates refuse permanent dismiss."""
    from ditaknet import database as db

    status = await get_update_status(force=False)
    target = _clean_version(version or status.get("latest_version"))
    if not target:
        return status
    if status.get("critical") and is_newer_version(target, settings.app_version):
        # Only allow snooze for critical
        return await snooze_update_banner()
    await db.set_app_setting(_KEY_DISMISSED, target)
    _CACHE["payload"] = None
    return await get_update_status(force=False)


# ── Background scheduler ─────────────────────────────────────────


async def _checker_loop() -> None:
    """First check after 5–10 minutes, then every N hours with jitter."""
    initial_delay = random.uniform(5 * 60, 10 * 60)
    logger.info("Update checker scheduled — first run in {:.0f}s", initial_delay)
    try:
        await asyncio.wait_for(_CHECKER_STOP.wait(), timeout=initial_delay)
        return
    except asyncio.TimeoutError:
        pass

    while not _CHECKER_STOP.is_set():
        try:
            if await _is_check_enabled():
                payload = await check_for_updates(force=True)
                if payload.get("update_available") and payload.get("show_banner"):
                    try:
                        from ditaknet.core.notifications_service import notify_update_available

                        await notify_update_available(payload)
                    except Exception as exc:
                        logger.debug("Update notify skipped: {}", exc)
            else:
                logger.debug("Update checker disabled — skipping")
        except Exception as exc:
            logger.warning("Update checker iteration failed (ignored): {}", exc)

        interval = _interval_hours() * 3600
        jitter = random.uniform(-0.15 * interval, 0.15 * interval)
        delay = max(600.0, interval + jitter)
        try:
            await asyncio.wait_for(_CHECKER_STOP.wait(), timeout=delay)
            return
        except asyncio.TimeoutError:
            continue


def start_update_checker() -> None:
    """Start background update checker (idempotent). Failures never block startup."""
    global _CHECKER_TASK
    if not settings.scheduler_enabled:
        logger.info("Update checker not started (scheduler disabled)")
        return
    if _CHECKER_TASK and not _CHECKER_TASK.done():
        return
    _CHECKER_STOP.clear()
    try:
        from ditaknet.resilience import create_background_task

        _CHECKER_TASK = create_background_task(_checker_loop(), name="update_checker")
    except Exception as exc:
        logger.warning("Could not start update checker: {}", exc)


async def stop_update_checker() -> None:
    global _CHECKER_TASK
    _CHECKER_STOP.set()
    task = _CHECKER_TASK
    _CHECKER_TASK = None
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
