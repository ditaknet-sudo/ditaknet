"""
First-run / zero-state detection and persisted setup wizard state.
"""

from __future__ import annotations

from typing import Any

from ditaknet import database as db

SETUP_STEPS = (
    "language",
    "purpose",
    "admin",
    "network",
    "subnet",
    "discovery",
    "import",
    "finish",
)

_LEGACY_STEP_MAP = {
    "package": "admin",
    "packages": "admin",
    "activate": "admin",
    "system": "network",
    "license": "admin",
    "notifications": "subnet",
}

MONITORING_USE_CASE_KEY = "monitoring_use_case"
SETUP_NETWORK_COUNT_KEY = "setup_network_count"
SETUP_DEVICE_COUNT_KEY = "setup_device_count"
SETUP_NETWORK_TYPE_KEY = "setup_network_type"
SETUP_SUBNET_KEY = "setup_selected_subnet"
SETUP_SCAN_ID_KEY = "setup_scan_id"
SETUP_IMPORTED_COUNT_KEY = "setup_imported_count"


async def get_setup_step() -> str:
    step = await db.get_app_setting("setup_step", "language")
    step = _LEGACY_STEP_MAP.get(step, step)
    return step if step in SETUP_STEPS else "language"


async def set_setup_step(step: str) -> None:
    if step in SETUP_STEPS:
        await db.set_app_setting("setup_step", step)


async def is_zero_state() -> bool:
    if await db.is_setup_complete():
        return False
    hosts = await db.list_hosts()
    return len(hosts) == 0


async def needs_setup_redirect() -> bool:
    return not await db.is_setup_complete()


async def get_setup_status() -> dict[str, Any]:
    return {
        "setup_complete": await db.is_setup_complete(),
        "zero_state": await is_zero_state(),
        "current_step": await get_setup_step(),
        "system_name": await db.get_app_setting("system_name") or "",
        "default_language": await db.get_app_setting("default_language") or "en",
        "admin_configured": bool(await db.get_app_setting("admin_username")),
        "telegram_configured": bool(await db.get_app_setting("telegram_bot_token")),
        "monitoring_use_case": await get_monitoring_use_case(),
        "access_edition": "PROFESSIONAL",
        "activation_required": False,
        "trial_available": False,
        "selected_subnet": await get_setup_subnet(),
        "discovery_scan_id": await get_setup_scan_id(),
        "imported_devices_count": await get_imported_count(),
    }


async def save_admin_credentials(username: str, password_hash: str) -> None:
    await db.set_app_setting("admin_username", username.strip())
    await db.set_app_setting("admin_password_hash", password_hash)


async def save_system_name(name: str) -> None:
    await db.set_app_setting("system_name", name.strip())


async def save_default_language(lang: str) -> None:
    await db.set_app_setting("default_language", lang)


async def save_monitoring_use_case(use_case: str) -> None:
    await db.set_app_setting(MONITORING_USE_CASE_KEY, use_case.strip())


async def get_monitoring_use_case() -> str:
    return await db.get_app_setting(MONITORING_USE_CASE_KEY, "") or ""


async def save_network_plan(
    *,
    network_count: str,
    device_count: str,
    network_type: str,
) -> None:
    await db.set_app_setting(SETUP_NETWORK_COUNT_KEY, network_count)
    await db.set_app_setting(SETUP_DEVICE_COUNT_KEY, device_count)
    await db.set_app_setting(SETUP_NETWORK_TYPE_KEY, network_type)


async def get_network_plan() -> dict[str, str]:
    return {
        "network_count": await db.get_app_setting(SETUP_NETWORK_COUNT_KEY, "") or "1",
        "device_count": await db.get_app_setting(SETUP_DEVICE_COUNT_KEY, "") or "100",
        "network_type": await db.get_app_setting(SETUP_NETWORK_TYPE_KEY, "") or "192.168",
    }


async def save_setup_subnet(subnet: str) -> None:
    await db.set_app_setting(SETUP_SUBNET_KEY, subnet.strip())
    from ditaknet.discovery.refresh import register_monitored_subnet

    await register_monitored_subnet(subnet.strip())


async def get_setup_subnet() -> str:
    return await db.get_app_setting(SETUP_SUBNET_KEY, "") or ""


async def save_setup_scan_id(scan_id: int) -> None:
    await db.set_app_setting(SETUP_SCAN_ID_KEY, str(scan_id))


async def get_setup_scan_id() -> int | None:
    raw = await db.get_app_setting(SETUP_SCAN_ID_KEY, "")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def save_imported_count(count: int) -> None:
    await db.set_app_setting(SETUP_IMPORTED_COUNT_KEY, str(count))


async def get_imported_count() -> int:
    raw = await db.get_app_setting(SETUP_IMPORTED_COUNT_KEY, "0")
    try:
        return int(raw)
    except ValueError:
        return 0


async def save_telegram_settings(bot_token: str, chat_id: str) -> None:
    await db.set_app_setting("telegram_bot_token", bot_token.strip())
    await db.set_app_setting("telegram_chat_id", chat_id.strip())


async def complete_setup() -> None:
    await db.mark_setup_complete()
    await db.set_app_setting("setup_step", "finish")
