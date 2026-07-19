"""Console notification fallback — ensures alerts are visible without Telegram."""

from __future__ import annotations

from loguru import logger

from ditaknet.notifications.base import BaseNotifier


class ConsoleNotifier(BaseNotifier):
    """Log alerts to stdout/log file when no external channel is configured."""

    name = "console"

    async def send(
        self,
        subject: str,
        message: str,
        severity: str = "warning",
    ) -> bool:
        # Structured log line is picked up by LOG_DIR rotation in containers.
        logger.warning("[ALERT:{}] {} — {}", severity.upper(), subject, message.replace("\n", " | "))
        return True
