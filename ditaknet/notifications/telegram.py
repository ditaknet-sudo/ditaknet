"""
Telegram notification channel (optional).

Disabled automatically when ``TELEGRAM_BOT_TOKEN`` or ``TELEGRAM_CHAT_ID`` is empty.
Token values must never appear in logs or API responses — only ``enabled`` flags in UI.
"""

from __future__ import annotations

import asyncio

import httpx
from loguru import logger

from ditaknet.config import settings
from ditaknet.notifications.base import BaseNotifier


class TelegramNotifier(BaseNotifier):
    """Send notifications to a Telegram chat via Bot API."""

    name = "telegram"

    TELEGRAM_API = "https://api.telegram.org"
    MAX_RETRIES = 3

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
    ):
        self.bot_token = bot_token or settings.telegram_bot_token
        self.chat_id = chat_id or settings.telegram_chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    async def send(
        self,
        subject: str,
        message: str,
        severity: str = "warning",
    ) -> bool:
        """Send a Telegram message with retry and backoff."""
        if not self.enabled:
            logger.debug("Telegram notifier disabled (no token/chat_id configured)")
            return False

        text = self._format_message(subject, message, severity)
        url = f"{self.TELEGRAM_API}/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.post(url, json=payload)

                    if resp.status_code == 200:
                        logger.info("Telegram alert sent: {}", subject)
                        return True

                    if resp.status_code == 429:
                        # Rate limited — respect Retry-After header
                        retry_after = resp.json().get("parameters", {}).get(
                            "retry_after", 2 ** attempt
                        )
                        logger.warning(
                            "Telegram rate limited, retrying in {}s", retry_after
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    logger.error(
                        "Telegram API error ({}): {}",
                        resp.status_code,
                        resp.text[:200],
                    )

            except httpx.TimeoutException:
                logger.warning(
                    "Telegram request timeout (attempt {}/{})", attempt, self.MAX_RETRIES
                )
            except Exception as exc:
                logger.error(
                    "Telegram send failed (attempt {}/{}): {}",
                    attempt,
                    self.MAX_RETRIES,
                    exc,
                )

            if attempt < self.MAX_RETRIES:
                await asyncio.sleep(2 ** attempt)

        logger.error("Telegram notification failed after {} attempts: {}", self.MAX_RETRIES, subject)
        return False

    @staticmethod
    def _format_message(subject: str, message: str, severity: str) -> str:
        """Build a formatted Telegram message."""
        severity_emoji = {
            "warning": "⚠️",
            "critical": "🚨",
            "recovery": "✅",
        }
        emoji = severity_emoji.get(severity, "ℹ️")

        return (
            f"{emoji} *{subject}*\n"
            f"{'─' * 28}\n"
            f"{message}\n"
        )
