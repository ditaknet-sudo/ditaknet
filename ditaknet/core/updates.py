"""Public-safe application update status helper."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from ditaknet.config import settings

_CACHE: dict[str, Any] = {"expires_at": 0.0, "payload": None}
_CACHE_SECONDS = 300


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _clean_version(value: str | None) -> str:
    raw = str(value or "").strip()
    if raw.lower().startswith("version "):
        raw = raw[8:].strip()
    if raw.lower().startswith("v"):
        raw = raw[1:].strip()
    return raw


def _version_key(value: str | None) -> tuple:
    cleaned = _clean_version(value)
    parts: list[Any] = []
    for chunk in re.split(r"([0-9]+)", cleaned):
        if not chunk:
            continue
        if chunk.isdigit():
            parts.append(int(chunk))
        else:
            parts.append(chunk.lower().strip(".-_+ "))
    return tuple(parts)


def is_newer_version(latest: str | None, current: str | None) -> bool:
    """Return True when latest appears newer than current."""
    latest_clean = _clean_version(latest)
    current_clean = _clean_version(current)
    if not latest_clean or not current_clean:
        return False
    return _version_key(latest_clean) > _version_key(current_clean)


def _base_payload(source: str, message: str = "") -> dict[str, Any]:
    return {
        "status": source,
        "source": source,
        "message": message,
        "checked_at": _now(),
        "current_version": settings.app_version,
        "current_image_tag": settings.image_tag.strip() or None,
        "latest_version": None,
        "latest_image_tag": None,
        "update_available": False,
        "release_url": settings.app_update_release_url.strip() or None,
    }


def _payload_from_latest_env() -> dict[str, Any] | None:
    latest = settings.app_latest_version.strip()
    latest_tag = settings.app_latest_image_tag.strip()
    if not latest and not latest_tag:
        return None
    payload = _base_payload("env")
    payload["latest_version"] = latest or None
    payload["latest_image_tag"] = latest_tag or None
    payload["update_available"] = is_newer_version(latest, settings.app_version)
    payload["status"] = "update_available" if payload["update_available"] else "up_to_date"
    return payload


def _normalize_notice_items(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        message = str(item.get("message") or item.get("body") or "").strip()
        if not title and not message:
            continue
        normalized.append(
            {
                "id": str(item.get("id") or "").strip() or None,
                "title": title,
                "message": message,
                "level": str(item.get("level") or "info").strip().lower(),
                "url": str(item.get("url") or item.get("link") or "").strip() or None,
                "valid_until": str(item.get("valid_until") or item.get("expires_at") or "").strip() or None,
            }
        )
    return normalized


def _payload_from_manifest(data: dict[str, Any]) -> dict[str, Any]:
    latest = str(
        data.get("version")
        or data.get("latest_version")
        or data.get("tag_name")
        or data.get("name")
        or ""
    ).strip()
    image_tag = str(data.get("image_tag") or data.get("latest_image_tag") or "").strip()
    release_url = str(
        data.get("release_url")
        or data.get("html_url")
        or data.get("url")
        or settings.app_update_release_url
        or ""
    ).strip()
    release_notes_text = str(
        data.get("release_notes")
        or data.get("release_notes_text")
        or data.get("notes")
        or ""
    ).strip()
    announcements = _normalize_notice_items(data.get("announcements"))
    promotions = _normalize_notice_items(data.get("promotions"))
    manifest_message = str(data.get("message") or data.get("title") or "").strip()
    payload = _base_payload("url")
    payload["latest_version"] = latest or None
    payload["latest_image_tag"] = image_tag or None
    payload["release_url"] = release_url or None
    payload["release_notes_text"] = release_notes_text or None
    payload["announcements"] = announcements
    payload["promotions"] = promotions
    payload["manifest_message"] = manifest_message or None
    payload["update_available"] = is_newer_version(latest, settings.app_version)
    payload["customer_notice_available"] = bool(
        announcements or promotions or release_notes_text or manifest_message
    )
    if payload["update_available"]:
        payload["status"] = "update_available"
    elif payload["customer_notice_available"]:
        payload["status"] = "notice_available"
    else:
        payload["status"] = "up_to_date"
    return payload


async def get_update_status(*, force: bool = False) -> dict[str, Any]:
    """Return current/latest version status without exposing secrets."""
    if not settings.app_update_check_enabled:
        payload = _base_payload("disabled", "Update checks are disabled.")
        return enrich_update_status(payload)

    env_payload = _payload_from_latest_env()
    if env_payload:
        return enrich_update_status(env_payload)

    url = settings.app_update_check_url.strip()
    if not url:
        payload = _base_payload(
            "not_configured",
            "Updates are delivered through Docker/GitHub image releases. Check your deployment documentation or contact your administrator.",
        )
        return enrich_update_status(payload)

    now = time.monotonic()
    if not force and _CACHE["payload"] is not None and now < float(_CACHE["expires_at"]):
        return enrich_update_status(dict(_CACHE["payload"]))

    try:
        async with httpx.AsyncClient(timeout=settings.app_update_check_timeout_seconds) as client:
            response = await client.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Update endpoint did not return a JSON object")
            payload = _payload_from_manifest(data)
    except Exception as exc:
        payload = _base_payload("error", f"{type(exc).__name__}: {exc}")

    _CACHE["payload"] = dict(payload)
    _CACHE["expires_at"] = now + _CACHE_SECONDS
    return enrich_update_status(payload)


def enrich_update_status(payload: dict[str, Any]) -> dict[str, Any]:
    """Add public API fields expected by the Updates page and notification center."""
    source = str(payload.get("source") or payload.get("status") or "")
    has_env = bool(settings.app_latest_version.strip() or settings.app_latest_image_tag.strip())
    has_url = bool(settings.app_update_check_url.strip())
    source_configured = settings.app_update_check_enabled and (has_env or has_url)
    error_message = ""
    if source == "error":
        error_message = "Could not check updates"
    tag = settings.image_tag.strip() or "stable"
    ghcr = settings.ghcr_image.strip() or None
    github_repo = settings.github_repository.strip() or None
    checked = payload.get("checked_at")
    return {
        **payload,
        "current_version": settings.app_version,
        "current_image_tag": settings.image_tag.strip() or None,
        "build_commit": settings.build_commit.strip() or None,
        "build_date": settings.release_build_date or settings.build_date.strip() or None,
        "update_channel": tag if tag in {"stable", "latest", "manual"} else "stable",
        "latest_version": payload.get("latest_version"),
        "update_available": bool(payload.get("update_available")),
        "customer_notice_available": bool(payload.get("customer_notice_available")),
        "release_notes_url": payload.get("release_url") or settings.app_update_release_url.strip() or None,
        "release_notes_text": payload.get("release_notes_text"),
        "announcements": payload.get("announcements") or [],
        "promotions": payload.get("promotions") or [],
        "manifest_message": payload.get("manifest_message"),
        "checked_at": checked,
        "last_checked": checked,
        "source_configured": source_configured,
        "error_message": error_message,
        "github_repository": github_repo,
        "github_repo_url": github_repo or settings.app_update_release_url.strip() or None,
        "ghcr_image": ghcr,
        "auto_update_enabled": False,
        "backup_before_update": True,
    }
