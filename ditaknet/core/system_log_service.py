"""Structured system log recording with secret redaction."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from ditaknet import database as db

APP_START_MONOTONIC = time.monotonic()
APP_START_AT = datetime.now(timezone.utc)

VALID_LEVELS = frozenset({"debug", "info", "warning", "error", "critical"})
VALID_CATEGORIES = frozenset(
    {
        "application",
        "monitoring",
        "discovery",
        "notification",
        "license",
        "security",
        "audit",
        "system",
    }
)

SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(SECRET_KEY|ADMIN_PASSWORD|TELEGRAM_BOT_TOKEN|POSTGRES_PASSWORD|"
        r"DATABASE_URL|LICENSE_PRIVATE_KEY|PRIVATE_KEY|API_KEY|JWT|BEARER|"
        r"ACTIVATION_CODE|activation_code|bot_token|password)\s*[=:]\s*(\S+)",
        re.IGNORECASE,
    ),
    re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-+/=]+", re.IGNORECASE),
    re.compile(r"(://[^:]+:)([^@]+)(@)"),
)

_SENSITIVE_KEY_RE = SECRET_PATTERNS[0]
_JWT_RE = SECRET_PATTERNS[1]
_BEARER_RE = SECRET_PATTERNS[2]
_URL_PASSWORD_RE = SECRET_PATTERNS[3]
_REDACTED = "[REDACTED]"


def uptime_seconds() -> int:
    return max(0, int(time.monotonic() - APP_START_MONOTONIC))


def redact_text(text: str | None) -> str:
    """Remove secrets from free-text log content."""
    if not text:
        return ""
    result = str(text)
    result = _SENSITIVE_KEY_RE.sub(r"\1=" + _REDACTED, result)
    result = _JWT_RE.sub(_REDACTED, result)
    result = _BEARER_RE.sub(f"Bearer {_REDACTED}", result)
    result = _URL_PASSWORD_RE.sub(r"\1" + _REDACTED + r"\3", result)
    return result


def redact_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        key_lower = str(key).lower()
        if any(
            token in key_lower
            for token in (
                "secret",
                "password",
                "token",
                "activation",
                "private_key",
                "api_key",
                "jwt",
                "code",
            )
        ):
            safe[key] = _REDACTED
        elif isinstance(value, str):
            safe[key] = redact_text(value)
        elif isinstance(value, dict):
            safe[key] = redact_metadata(value)
        else:
            safe[key] = value
    return safe


async def record(
    level: str,
    category: str,
    event_type: str,
    message: str,
    *,
    source: str = "",
    entity_type: str = "",
    entity_id: str | int | None = None,
    user_id: str = "",
    ip_address: str = "",
    metadata: dict[str, Any] | None = None,
    is_sensitive: bool = False,
) -> dict | None:
    """Persist a sanitized system log entry. Never raises to callers."""
    try:
        level_norm = level.lower() if level.lower() in VALID_LEVELS else "info"
        category_norm = category.lower() if category.lower() in VALID_CATEGORIES else "application"
        safe_message = redact_text(message)
        safe_meta = redact_metadata(metadata)
        if is_sensitive:
            safe_message = _REDACTED
            safe_meta = {"redacted": True}
        return await db.create_system_log(
            level=level_norm,
            category=category_norm,
            event_type=event_type,
            message=safe_message,
            source=source,
            entity_type=entity_type,
            entity_id="" if entity_id is None else str(entity_id),
            user_id=user_id,
            ip_address=ip_address,
            metadata_json=json.dumps(safe_meta, default=str),
            is_sensitive=1 if is_sensitive else 0,
        )
    except Exception as exc:
        logger.debug("Failed to write system log: {}", exc)
        return None


async def record_audit_from_legacy(audit_row: dict) -> None:
    await record(
        "info",
        "audit",
        str(audit_row.get("action") or "audit_event"),
        redact_text(str(audit_row.get("detail") or audit_row.get("action") or "")),
        source="audit_logs",
        entity_type=str(audit_row.get("resource") or ""),
        entity_id=audit_row.get("resource_id"),
        user_id=str(audit_row.get("actor") or "system"),
        ip_address=str(audit_row.get("ip_address") or ""),
        metadata={
            "action": audit_row.get("action"),
            "resource": audit_row.get("resource"),
        },
    )


async def record_check_started(service: dict, host_name: str) -> None:
    await record(
        "info",
        "monitoring",
        "check_started",
        f"Monitoring check started: {service.get('check_type')} {service.get('name')} ({host_name})",
        source="scheduler",
        entity_type="service",
        entity_id=service.get("id"),
        metadata={
            "check_type": service.get("check_type"),
            "target": service.get("target"),
            "host_name": host_name,
        },
    )


async def record_check_completed(
    service: dict,
    host_name: str,
    *,
    status: str,
    response_time_ms: float | None,
    message: str,
) -> None:
    level = "info" if status == "ok" else "warning" if status == "warning" else "error"
    event = "check_completed" if status == "ok" else "check_failed"
    await record(
        level,
        "monitoring",
        event,
        f"Service {status.upper()}: {service.get('name')} on {host_name} — {message}",
        source="scheduler",
        entity_type="service",
        entity_id=service.get("id"),
        metadata={
            "status": status,
            "response_time_ms": response_time_ms,
            "check_type": service.get("check_type"),
            "host_name": host_name,
        },
    )


async def record_state_change(
    service_id: int,
    service_name: str,
    host_name: str,
    old_state: str,
    new_state: str,
) -> None:
    level = "info"
    if new_state == "critical":
        level = "error"
    elif new_state == "warning":
        level = "warning"
    elif new_state == "ok" and old_state in {"warning", "critical"}:
        await record(
            "info",
            "monitoring",
            "recovery_detected",
            f"Recovery detected: {service_name} on {host_name} ({old_state} → {new_state})",
            source="state_engine",
            entity_type="service",
            entity_id=service_id,
            metadata={"old_state": old_state, "new_state": new_state, "host_name": host_name},
        )
        return
    await record(
        level,
        "monitoring",
        "service_state_changed",
        f"Service state changed: {service_name} on {host_name} ({old_state} → {new_state})",
        source="state_engine",
        entity_type="service",
        entity_id=service_id,
        metadata={"old_state": old_state, "new_state": new_state, "host_name": host_name},
    )


async def record_notification(
    *,
    event_type: str,
    message: str,
    level: str = "info",
    notifier: str = "",
    success: bool = True,
) -> None:
    await record(
        "error" if not success else level,
        "notification",
        event_type,
        message,
        source=notifier or "notifier",
        metadata={"success": success},
    )


async def record_discovery_event(
    scan_id: int,
    event_type: str,
    message: str,
    *,
    level: str = "info",
    ip_address: str = "",
) -> None:
    await record(
        level,
        "discovery",
        event_type,
        message,
        source="discovery_scheduler",
        entity_type="scan",
        entity_id=scan_id,
        ip_address=ip_address,
    )


async def record_license_event(
    event_type: str,
    message: str,
    *,
    level: str = "info",
    user_id: str = "",
    metadata: dict | None = None,
) -> None:
    await record(
        level,
        "license",
        event_type,
        redact_text(message),
        source="licensing",
        user_id=user_id,
        metadata=redact_metadata(metadata),
    )
