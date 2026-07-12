"""
DitakNet — Plugin base class.

Plugins can extend the monitoring server with custom checks,
notification channels, or API endpoints.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from fastapi import APIRouter

    from ditaknet.core.checks.base import BaseCheck
    from ditaknet.notifications.base import BaseNotifier


class BasePlugin(ABC):
    """Abstract base class for DitakNet plugins.

    Subclasses must implement ``on_load`` and ``on_unload``.
    They can optionally register checks, notifiers, and API routes.
    """

    #: Unique plugin name
    name: str = "base_plugin"
    #: SemVer version string
    version: str = "0.0.0"
    #: Brief description
    description: str = ""

    def __init__(self):
        self._checks: dict[str, type[BaseCheck]] = {}
        self._notifiers: list[BaseNotifier] = []
        self._routers: list[APIRouter] = []

    # ── Lifecycle ─────────────────────────────────────────

    @abstractmethod
    async def on_load(self, app_context: dict) -> None:
        """Called when the plugin is loaded.

        Parameters
        ----------
        app_context:
            Dict containing references to core components:
            ``state_engine``, ``alert_engine``, ``scheduler``, ``settings``.
        """
        ...

    @abstractmethod
    async def on_unload(self) -> None:
        """Called when the plugin is unloaded / server is shutting down."""
        ...

    # ── Registration Helpers ──────────────────────────────

    def register_check(self, check_type: str, check_class: type[BaseCheck]) -> None:
        """Register a custom check type.

        The check will be available as ``check_type`` in service definitions.
        """
        self._checks[check_type] = check_class

    def register_notifier(self, notifier: BaseNotifier) -> None:
        """Register a custom notification channel."""
        self._notifiers.append(notifier)

    def register_api_router(self, router: APIRouter, prefix: Optional[str] = None) -> None:
        """Register a custom API router to be mounted on the app."""
        self._routers.append(router)

    # ── Accessors ─────────────────────────────────────────

    @property
    def checks(self) -> dict[str, type]:
        return self._checks

    @property
    def notifiers(self) -> list:
        return self._notifiers

    @property
    def routers(self) -> list:
        return self._routers
