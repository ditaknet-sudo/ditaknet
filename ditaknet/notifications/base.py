"""
Notification channel interface.

Implement ``BaseNotifier`` and register on ``AlertEngine`` during startup
(see ``main.lifespan``). ``ConsoleNotifier`` is always registered as fallback;
``TelegramNotifier`` is added only when token + chat id are configured.

Notifiers must never log raw tokens or passwords.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseNotifier(ABC):
    """Abstract notification channel."""

    #: Human-readable name for logging
    name: str = "base"

    @abstractmethod
    async def send(
        self,
        subject: str,
        message: str,
        severity: str = "warning",
    ) -> bool:
        """Send a notification.

        Parameters
        ----------
        subject:
            Short summary / subject line.
        message:
            Full alert message body.
        severity:
            Alert severity (warning, critical, recovery).

        Returns
        -------
        True if the notification was sent successfully.
        """
        ...
