"""Profile-level troubleshooting hint keys (resolved via i18n at display time)."""

from __future__ import annotations

from ditaknet.profiles.device_profiles import get_profile, normalize_device_type


def profile_troubleshoot_hint(device_type: str, lang: str = "en") -> str:
    from ditaknet.i18n import translate

    profile = get_profile(normalize_device_type(device_type))
    if profile.troubleshoot_hint_key:
        return translate(profile.troubleshoot_hint_key, lang)
    return translate("profile.unknown.troubleshoot", lang)
