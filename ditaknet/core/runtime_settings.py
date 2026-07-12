"""Runtime settings persisted during setup (override env when set in DB)."""

from __future__ import annotations

from ditaknet import database as db
from ditaknet.config import settings


async def get_system_name() -> str:
    stored = await db.get_app_setting("system_name")
    return stored or settings.app_name


async def get_telegram_config() -> tuple[str, str]:
    token = await db.get_app_setting("telegram_bot_token") or settings.telegram_bot_token
    chat_id = await db.get_app_setting("telegram_chat_id") or settings.telegram_chat_id
    return token or "", chat_id or ""


async def telegram_enabled() -> bool:
    token, chat_id = await get_telegram_config()
    return bool(token and chat_id)
