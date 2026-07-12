"""
DitakNet — Plugin Manager.

Discovers, loads, and manages the lifecycle of plugins.
Plugins are Python packages in the configured PLUGIN_DIR that
contain a ``plugin.py`` with a ``Plugin(BasePlugin)`` class.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ditaknet.config import settings
from ditaknet.plugins.base import BasePlugin

if TYPE_CHECKING:
    pass


class PluginManager:
    """Discovers and manages plugins."""

    def __init__(self):
        self._plugins: dict[str, BasePlugin] = {}

    @property
    def plugins(self) -> dict[str, BasePlugin]:
        return dict(self._plugins)

    # ── Discovery ─────────────────────────────────────────

    def discover(self, plugin_dir: str | None = None) -> list[str]:
        """Scan the plugin directory for loadable plugins.

        Returns a list of discovered plugin names.
        """
        base = Path(plugin_dir or settings.plugin_dir)
        if not base.exists():
            logger.debug("Plugin directory '{}' does not exist, skipping", base)
            return []

        discovered = []
        for child in base.iterdir():
            if child.is_dir() and (child / "plugin.py").exists():
                discovered.append(child.name)
        if discovered:
            logger.info("Discovered {} plugin(s): {}", len(discovered), discovered)
        return discovered

    # ── Loading ───────────────────────────────────────────

    async def load_all(self, app_context: dict) -> None:
        """Discover and load all plugins."""
        names = self.discover()
        for name in names:
            try:
                await self.load_plugin(name, app_context)
            except Exception as exc:
                logger.error("Failed to load plugin '{}': {}", name, exc)

    async def load_plugin(self, name: str, app_context: dict) -> BasePlugin | None:
        """Load a single plugin by name."""
        plugin_dir = Path(settings.plugin_dir)
        plugin_file = plugin_dir / name / "plugin.py"

        if not plugin_file.exists():
            logger.warning("Plugin file not found: {}", plugin_file)
            return None

        try:
            # Dynamic import
            module_name = f"ditaknet_plugin_{name}"
            spec = importlib.util.spec_from_file_location(module_name, str(plugin_file))
            if spec is None or spec.loader is None:
                logger.error("Cannot create module spec for plugin '{}'", name)
                return None

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # Look for the Plugin class
            plugin_cls = getattr(module, "Plugin", None)
            if plugin_cls is None or not issubclass(plugin_cls, BasePlugin):
                logger.error(
                    "Plugin '{}' must have a Plugin class inheriting BasePlugin", name
                )
                return None

            plugin: BasePlugin = plugin_cls()
            await plugin.on_load(app_context)

            # Register plugin's contributions
            self._register_contributions(plugin, app_context)
            self._plugins[name] = plugin
            logger.info(
                "Loaded plugin '{}' v{}: {}",
                plugin.name,
                plugin.version,
                plugin.description,
            )
            return plugin

        except Exception as exc:
            logger.error("Error loading plugin '{}': {}", name, exc)
            return None

    # ── Unloading ─────────────────────────────────────────

    async def unload_all(self) -> None:
        """Unload all loaded plugins."""
        for name, plugin in list(self._plugins.items()):
            try:
                await plugin.on_unload()
                logger.info("Unloaded plugin '{}'", name)
            except Exception as exc:
                logger.error("Error unloading plugin '{}': {}", name, exc)
        self._plugins.clear()

    # ── Internals ─────────────────────────────────────────

    def _register_contributions(self, plugin: BasePlugin, app_context: dict) -> None:
        """Wire up the plugin's registered checks, notifiers, and routers."""
        # Register custom checks
        from ditaknet.core.scheduler import CHECK_REGISTRY
        for check_type, check_cls in plugin.checks.items():
            CHECK_REGISTRY[check_type] = check_cls
            logger.info("Plugin '{}' registered check type '{}'", plugin.name, check_type)

        # Register notifiers
        alert_engine = app_context.get("alert_engine")
        if alert_engine:
            for notifier in plugin.notifiers:
                alert_engine.register_notifier(notifier)

        # Register API routers
        app = app_context.get("app")
        if app:
            for router in plugin.routers:
                app.include_router(router, prefix="/api/v1/plugins")
