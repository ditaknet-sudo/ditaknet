"""Safe build/version metadata for health and about endpoints."""

from __future__ import annotations

from typing import Any

from ditaknet.config import settings

NOT_PROVIDED = "Not provided by build."


def _display(value: str | None) -> str | None:
    raw = str(value or "").strip()
    return raw or None


def _display_or_notice(value: str | None) -> str:
    raw = str(value or "").strip()
    return raw if raw else NOT_PROVIDED


def build_metadata(*, friendly_missing: bool = False) -> dict[str, Any]:
    """Return public-safe build metadata without secrets."""
    commit = _display(settings.build_commit)
    build_date = _display(settings.release_build_date or settings.build_date)
    image_tag = _display(settings.image_tag)
    github_repo = _display(getattr(settings, "github_repository", "") or "")
    ghcr_image = _display(getattr(settings, "ghcr_image", "") or "")

    if friendly_missing:
        return {
            "app_version": settings.app_version,
            "version": settings.app_version,
            "build_commit": _display_or_notice(commit),
            "build_date": _display_or_notice(build_date),
            "image_tag": _display_or_notice(image_tag),
            "github_repository": _display_or_notice(github_repo),
            "ghcr_image": _display_or_notice(ghcr_image),
        }

    return {
        "app_version": settings.app_version,
        "version": settings.app_version,
        "build_commit": commit,
        "build_date": build_date,
        "image_tag": image_tag,
        "github_repository": github_repo,
        "ghcr_image": ghcr_image,
        "build_commit_provided": bool(commit),
        "build_date_provided": bool(build_date),
        "image_tag_provided": bool(image_tag),
    }
