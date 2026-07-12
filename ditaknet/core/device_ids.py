"""Device identifier parsing for unified device routes."""

from __future__ import annotations

import re

_DEVICE_ID_RE = re.compile(r"^(?P<source>host|agent)-(?P<numeric>\d+)$", re.IGNORECASE)


def parse_device_id(device_id: str) -> tuple[str, int]:
    """Parse ``host-12`` / ``agent-3`` or plain numeric host id."""
    raw = str(device_id or "").strip()
    if not raw:
        raise ValueError("Device id is required")
    if raw.isdigit():
        return "host", int(raw)
    match = _DEVICE_ID_RE.match(raw)
    if not match:
        raise ValueError(f"Invalid device id: {device_id}")
    return match.group("source").lower(), int(match.group("numeric"))


def format_device_id(source: str, numeric_id: int) -> str:
    return f"{source.lower()}-{int(numeric_id)}"
