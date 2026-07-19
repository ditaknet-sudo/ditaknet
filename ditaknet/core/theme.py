"""Theme preference schema and resolution (mirrors static/js/theme.js).

Frontend persists prefs in localStorage. This module is the shared contract
for tests and a future backend user-settings sync.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, time
from typing import Any, Literal

ThemeMode = Literal["light", "dark", "system", "auto"]
ActiveTheme = Literal["light", "dark"]

STORAGE_KEY = "ditaknet.theme.v1"
DEFAULT_MODE: ThemeMode = "system"
DEFAULT_DAY_STARTS = "07:00"
DEFAULT_NIGHT_STARTS = "19:00"

_HHMM_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


@dataclass(frozen=True)
class ThemePrefs:
    mode: ThemeMode = DEFAULT_MODE
    day_starts: str = DEFAULT_DAY_STARTS
    night_starts: str = DEFAULT_NIGHT_STARTS

    def to_storage_dict(self) -> dict[str, str]:
        return {
            "mode": self.mode,
            "dayStarts": self.day_starts,
            "nightStarts": self.night_starts,
        }


def parse_hhmm(value: Any, fallback: str) -> str:
    raw = str(value or "").strip()
    match = _HHMM_RE.match(raw)
    if not match:
        return fallback
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return fallback
    return f"{hour:02d}:{minute:02d}"


def normalize_prefs(raw: dict[str, Any] | None) -> ThemePrefs:
    if not raw:
        return ThemePrefs()
    mode = str(raw.get("mode") or DEFAULT_MODE).lower()
    if mode not in {"light", "dark", "system", "auto"}:
        mode = DEFAULT_MODE
    day = parse_hhmm(raw.get("dayStarts") or raw.get("day_starts"), DEFAULT_DAY_STARTS)
    night = parse_hhmm(raw.get("nightStarts") or raw.get("night_starts"), DEFAULT_NIGHT_STARTS)
    return ThemePrefs(mode=mode, day_starts=day, night_starts=night)  # type: ignore[arg-type]


def _minutes(hhmm: str) -> int:
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)


def resolve_auto_theme(
    day_starts: str = DEFAULT_DAY_STARTS,
    night_starts: str = DEFAULT_NIGHT_STARTS,
    *,
    now: datetime | None = None,
) -> ActiveTheme:
    """Return light/dark for auto schedule using local wall-clock time."""
    day = parse_hhmm(day_starts, DEFAULT_DAY_STARTS)
    night = parse_hhmm(night_starts, DEFAULT_NIGHT_STARTS)
    current = now or datetime.now().astimezone()
    mins = current.hour * 60 + current.minute
    day_min = _minutes(day)
    night_min = _minutes(night)
    if day_min == night_min:
        return "light"
    if day_min < night_min:
        return "light" if day_min <= mins < night_min else "dark"
    return "light" if mins >= day_min or mins < night_min else "dark"


def resolve_active_theme(
    prefs: ThemePrefs | dict[str, Any] | None,
    *,
    system_dark: bool = False,
    now: datetime | None = None,
) -> ActiveTheme:
    normalized = prefs if isinstance(prefs, ThemePrefs) else normalize_prefs(prefs)
    if normalized.mode == "light":
        return "light"
    if normalized.mode == "dark":
        return "dark"
    if normalized.mode == "auto":
        return resolve_auto_theme(normalized.day_starts, normalized.night_starts, now=now)
    return "dark" if system_dark else "light"


def describe_prefs(prefs: ThemePrefs | dict[str, Any] | None) -> str:
    normalized = prefs if isinstance(prefs, ThemePrefs) else normalize_prefs(prefs)
    if normalized.mode == "light":
        return "Light"
    if normalized.mode == "dark":
        return "Dark"
    if normalized.mode == "system":
        return "System"
    return (
        f"Auto: Light {normalized.day_starts}–{normalized.night_starts}, "
        f"Dark {normalized.night_starts}–{normalized.day_starts}"
    )


def prefs_asdict(prefs: ThemePrefs) -> dict[str, Any]:
    return asdict(prefs)


def time_from_hhmm(hhmm: str) -> time:
    hour, minute = parse_hhmm(hhmm, DEFAULT_DAY_STARTS).split(":")
    return time(hour=int(hour), minute=int(minute))
