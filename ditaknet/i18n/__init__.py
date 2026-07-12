"""UI translation loader — hy, en, ru with English fallback."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_LOCALES_DIR = Path(__file__).resolve().parents[2] / "app" / "i18n"
_DEFAULT_LANG = "en"
_SUPPORTED = ("hy", "en", "ru")


@lru_cache(maxsize=8)
def _load_locale(lang: str) -> dict[str, str]:
    code = lang if lang in _SUPPORTED else _DEFAULT_LANG
    path = _LOCALES_DIR / f"{code}.json"
    if not path.is_file():
        path = _LOCALES_DIR / f"{_DEFAULT_LANG}.json"
    with path.open(encoding="utf-8") as fh:
        data: dict[str, str] = json.load(fh)
    if code == _DEFAULT_LANG:
        return data
    en_path = _LOCALES_DIR / f"{_DEFAULT_LANG}.json"
    with en_path.open(encoding="utf-8") as fh:
        en_data: dict[str, str] = json.load(fh)
    return {**en_data, **data}


def clear_locale_cache() -> None:
    """Clear cached locale files (use after i18n JSON updates)."""
    _load_locale.cache_clear()


def translate(key: str, lang: str = "en", **kwargs: Any) -> str:
    """Return translated string; falls back to English then the key itself."""
    catalog = _load_locale(lang)
    text = catalog.get(key) or _load_locale(_DEFAULT_LANG).get(key) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError):
            return text
    return text


def supported_languages() -> list[str]:
    return list(_SUPPORTED)


def language_label(code: str) -> str:
    labels = {
        "hy": "Հայերեն",
        "en": "English",
        "ru": "Русский",
    }
    return labels.get(code, code.upper())
