"""Form parsing and validation helpers for the web dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


def parse_checkbox(value: Optional[str]) -> bool:
    """Parse HTML checkbox values."""
    if value is None:
        return False
    return str(value).lower() in ("true", "on", "1", "yes")


def parse_optional_int(value: Optional[str]) -> Optional[int]:
    """Parse optional integer form fields."""
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


@dataclass
class HostFormData:
    name: str
    address: str
    host_type: str
    location: str
    tags: str
    enabled: bool


def validate_host_form(
    *,
    name: str,
    address: str,
    host_type: str,
    location: str,
    tags: str,
    enabled: bool,
) -> tuple[Optional[HostFormData], Optional[str]]:
    """Validate host create/edit form fields."""
    if not name.strip():
        return None, "Host name is required."
    if not address.strip():
        return None, "Address or IP is required."
    return HostFormData(
        name=name.strip(),
        address=address.strip(),
        host_type=(host_type or "server").strip(),
        location=location.strip(),
        tags=tags.strip(),
        enabled=enabled,
    ), None


def host_form_as_dict(form: HostFormData) -> dict:
    """Convert host form data to a template-friendly dict."""
    return {
        "name": form.name,
        "address": form.address,
        "host_type": form.host_type,
        "location": form.location,
        "tags": form.tags,
        "enabled": form.enabled,
    }


@dataclass
class ServiceFormData:
    host_id: int
    name: str
    check_type: str
    target: str
    port: Optional[int]
    url: str
    interval_seconds: int
    timeout_seconds: int
    retry_count: int
    max_attempts: int
    enabled: bool
    expected_status_code: int = 200


def validate_service_form(
    *,
    host_id: int,
    name: str,
    check_type: str,
    target: str,
    port: Optional[int],
    url: str,
    interval_seconds: int,
    timeout_seconds: int,
    retry_count: int,
    max_attempts: int,
    enabled: bool,
) -> tuple[Optional[ServiceFormData], Optional[str]]:
    """Validate service create/edit form fields."""
    if not name.strip():
        return None, "Service name is required."
    if interval_seconds <= 0:
        return None, "Interval must be a positive number."
    if timeout_seconds <= 0:
        return None, "Timeout must be a positive number."

    check_type = check_type.lower()
    resolved_target = target.strip()
    resolved_port = port

    if check_type == "ping":
        if not resolved_target:
            return None, "Ping checks require a host or address."
    elif check_type == "tcp":
        if not resolved_target:
            return None, "TCP checks require a host or address."
        if not resolved_port:
            return None, "TCP checks require a port."
    elif check_type == "http":
        resolved_target = (url or target).strip()
        if not resolved_target:
            return None, "HTTP checks require a URL."
    elif check_type == "command":
        return None, "Command checks are not available yet. Use Ping, TCP, or HTTP checks instead."
    else:
        return None, f"Unsupported check type: {check_type}"

    return ServiceFormData(
        host_id=host_id,
        name=name.strip(),
        check_type=check_type,
        target=resolved_target,
        port=resolved_port,
        url=url.strip() if url else resolved_target,
        interval_seconds=interval_seconds,
        timeout_seconds=timeout_seconds,
        retry_count=retry_count,
        max_attempts=max_attempts,
        enabled=enabled,
    ), None


def service_form_as_dict(form: ServiceFormData, *, service_id: Optional[int] = None) -> dict:
    """Convert service form data to a template-friendly dict."""
    data = {
        "host_id": form.host_id,
        "name": form.name,
        "check_type": form.check_type,
        "target": form.target,
        "port": form.port,
        "interval_seconds": form.interval_seconds,
        "timeout_seconds": form.timeout_seconds,
        "retry_count": form.retry_count,
        "max_attempts": form.max_attempts,
        "enabled": form.enabled,
    }
    if service_id is not None:
        data["id"] = service_id
    return data
