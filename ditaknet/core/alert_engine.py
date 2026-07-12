"""
Alert engine: turns state transitions into DB alerts and notifier dispatch.

Alert policy (see ``StateEngine`` + ``_should_create_alert``):
  - SOFT (warning): logged in state_log, no user-facing alert record
  - HARD (critical): creates alert + notification (subject to cooldown)
  - RECOVERY: notifies when returning from critical or active incident

Cooldown suppresses repeat hard alerts; recovery bypasses cooldown.
Maintenance mode suppresses dispatch but still logs transitions.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from loguru import logger

from ditaknet import database as db
from ditaknet.config import settings
from ditaknet.models import AlertSeverity, ServiceState

if TYPE_CHECKING:
    from ditaknet.core.checks.base import CheckResponse
    from ditaknet.notifications.base import BaseNotifier


class AlertEngine:
    """Processes state changes into alerts and dispatches notifications."""

    def __init__(self, cooldown_seconds: int | None = None):
        self.cooldown_seconds = (
            settings.alert_cooldown_seconds if cooldown_seconds is None else cooldown_seconds
        )
        self._notifiers: list[BaseNotifier] = []
        self._last_alert: dict[int, float] = {}
        self._active_incidents: set[int] = set()

    def register_notifier(self, notifier: BaseNotifier) -> None:
        """Add a notification channel."""
        self._notifiers.append(notifier)
        logger.info("Registered notifier: {}", notifier.name)

    def clear_notifiers(self) -> None:
        """Remove all notification channels (used in tests)."""
        self._notifiers.clear()
        self._last_alert.clear()
        self._active_incidents.clear()

    def clear_runtime_state(self) -> None:
        """Clear in-memory alert cooldown/incident tracking after a factory reset."""
        self._last_alert.clear()
        self._active_incidents.clear()

    async def process_state_change(
        self,
        service_id: int,
        service_name: str,
        host_name: str,
        old_state: ServiceState,
        new_state: ServiceState,
        check_response: CheckResponse,
        state_type: str | None = None,
    ) -> bool:
        """Handle a state transition.

        Returns True if an alert was created and dispatched. Soft transitions
        are still written to the state log but do not create alert records.
        """
        transition_kind = self._classify_transition(old_state, new_state, state_type)
        severity = self._map_severity(new_state)
        message = self._build_message(
            service_name=service_name,
            host_name=host_name,
            old_state=old_state,
            new_state=new_state,
            check_response=check_response,
        )

        await self._log_state_change(service_id, old_state, new_state, check_response)

        if await self._maintenance_mode_enabled():
            logger.info(
                "Alert suppressed for service {} (maintenance mode active)",
                service_id,
            )
            return False

        if not self._should_create_alert(service_id, old_state, new_state, transition_kind):
            logger.debug(
                "State transition for service {} logged without alert ({})",
                service_id,
                transition_kind,
            )
            return False

        if severity != AlertSeverity.RECOVERY and self._in_cooldown(service_id):
            logger.debug(
                "Alert suppressed for service {} (cooldown active)", service_id
            )
            return False

        alert_type = f"state_change:{old_state.value}->{new_state.value}"
        alert_row = None
        try:
            alert_row = await db.create_alert(
                service_id=service_id,
                alert_type=alert_type,
                message=message,
                severity=severity.value,
            )
            try:
                from ditaknet.core.system_log_service import record

                await record(
                    "warning" if severity == AlertSeverity.WARNING else "error",
                    "monitoring",
                    "alert_created",
                    f"Alert created: {service_name} on {host_name} ({new_state.value})",
                    source="alert_engine",
                    entity_type="service",
                    entity_id=service_id,
                    metadata={"alert_id": alert_row.get("id") if alert_row else None},
                )
            except Exception:
                pass
        except Exception as exc:
            logger.error("Failed to persist alert for service {}: {}", service_id, exc)

        if severity == AlertSeverity.CRITICAL and transition_kind == "hard" and alert_row:
            await self._maybe_create_maintenance_task(
                alert_row, service_id, service_name, host_name, message
            )

        if severity == AlertSeverity.RECOVERY:
            self._active_incidents.discard(service_id)
        else:
            self._active_incidents.add(service_id)
            self._last_alert[service_id] = time.time()

        await self._dispatch(
            service_name=service_name,
            host_name=host_name,
            old_state=old_state,
            new_state=new_state,
            severity=severity,
            message=message,
        )

        return True

    async def acknowledge(self, alert_id: int) -> dict | None:
        """Mark an alert as acknowledged."""
        return await db.acknowledge_alert(alert_id)

    async def _log_state_change(
        self,
        service_id: int,
        old_state: ServiceState,
        new_state: ServiceState,
        check_response: CheckResponse,
    ) -> None:
        try:
            await db.insert_state_change(
                service_id=service_id,
                old_state=old_state.value,
                new_state=new_state.value,
                reason=check_response.message,
            )
        except Exception as exc:
            logger.error("Failed to log state change for service {}: {}", service_id, exc)

    async def _maintenance_mode_enabled(self) -> bool:
        try:
            return await db.get_maintenance_mode(settings.maintenance_mode)
        except Exception:
            return settings.maintenance_mode

    def _in_cooldown(self, service_id: int) -> bool:
        """Check if a service is within the alert cooldown window."""
        last = self._last_alert.get(service_id)
        if last is None:
            return False
        return (time.time() - last) < self.cooldown_seconds

    @staticmethod
    def _classify_transition(
        old_state: ServiceState,
        new_state: ServiceState,
        state_type: str | None,
    ) -> str:
        if state_type in {"soft", "hard", "recovery"}:
            return state_type
        if new_state == ServiceState.OK and old_state in {
            ServiceState.WARNING,
            ServiceState.CRITICAL,
        }:
            return "recovery"
        if new_state == ServiceState.CRITICAL:
            return "hard"
        if new_state == ServiceState.WARNING:
            return "soft"
        return "stable"

    def _should_create_alert(
        self,
        service_id: int,
        old_state: ServiceState,
        new_state: ServiceState,
        transition_kind: str,
    ) -> bool:
        """Only hard failures and meaningful recoveries become alert records.

        Soft warning transitions are intentionally silent to reduce noise.
        """
        if transition_kind == "hard" and new_state == ServiceState.CRITICAL:
            return True
        if transition_kind != "recovery" or new_state != ServiceState.OK:
            return False
        return old_state == ServiceState.CRITICAL or service_id in self._active_incidents

    @staticmethod
    def _map_severity(new_state: ServiceState) -> AlertSeverity:
        """Map a service state to an alert severity."""
        mapping = {
            ServiceState.WARNING: AlertSeverity.WARNING,
            ServiceState.CRITICAL: AlertSeverity.CRITICAL,
            ServiceState.OK: AlertSeverity.RECOVERY,
        }
        return mapping.get(new_state, AlertSeverity.WARNING)

    @staticmethod
    def _build_message(
        service_name: str,
        host_name: str,
        old_state: ServiceState,
        new_state: ServiceState,
        check_response: CheckResponse,
    ) -> str:
        """Construct a human-readable alert message."""
        icon = {
            ServiceState.OK: "[OK]",
            ServiceState.WARNING: "[WARNING]",
            ServiceState.CRITICAL: "[CRITICAL]",
            ServiceState.UNKNOWN: "[UNKNOWN]",
        }.get(new_state, "[UNKNOWN]")

        lines = [
            f"{icon} **{new_state.value.upper()}** - {service_name}",
            f"Host: {host_name}",
            f"Transition: {old_state.value} -> {new_state.value}",
        ]
        if check_response.response_time_ms:
            lines.append(f"Response time: {check_response.response_time_ms:.1f}ms")
        if check_response.message:
            lines.append(f"Details: {check_response.message}")

        return "\n".join(lines)

    async def _maybe_create_maintenance_task(
        self,
        alert_row: dict,
        service_id: int,
        service_name: str,
        host_name: str,
        message: str,
    ) -> None:
        """Optional maintenance task for critical alerts — guides remote triage."""
        try:
            from ditaknet.assistant.recommendations import maintenance_recommendation_for_alert

            service = await db.get_service(service_id)
            host = await db.get_host(service["host_id"]) if service else None
            recommendation = await maintenance_recommendation_for_alert(
                alert_row, host, service, lang="en"
            )
            title = f"{host_name} — {service_name}"
            await db.create_maintenance_task(
                title=title[:255],
                device_id=host["id"] if host else None,
                alert_id=alert_row["id"],
                priority="high",
                recommendation=recommendation,
            )
        except Exception as exc:
            logger.debug("Maintenance task not created: {}", exc)

    async def _dispatch(
        self,
        service_name: str,
        host_name: str,
        old_state: ServiceState,
        new_state: ServiceState,
        severity: AlertSeverity,
        message: str,
    ) -> None:
        """Send alert to all registered notification channels."""
        for notifier in self._notifiers:
            try:
                await notifier.send(
                    subject=f"[{severity.value.upper()}] {service_name} on {host_name}",
                    message=message,
                    severity=severity.value,
                )
                try:
                    from ditaknet.core.system_log_service import record_notification

                    await record_notification(
                        event_type="notification_sent",
                        message=f"Notification sent via {notifier.name}: {service_name} on {host_name}",
                        level="info",
                        notifier=notifier.name,
                        success=True,
                    )
                except Exception:
                    pass
            except Exception as exc:
                logger.error(
                    "Notifier '{}' failed for service '{}': {}",
                    notifier.name,
                    service_name,
                    exc,
                )
                try:
                    from ditaknet.core.system_log_service import record_notification

                    await record_notification(
                        event_type="notification_failed",
                        message=f"Notification failed via {notifier.name}: {service_name} on {host_name}",
                        notifier=notifier.name,
                        success=False,
                    )
                except Exception:
                    pass
                if notifier.name.lower() == "console":
                    try:
                        from ditaknet.core.system_log_service import record_notification

                        await record_notification(
                            event_type="notification_fallback",
                            message=f"Console fallback used for {service_name} on {host_name}",
                            level="warning",
                            notifier="console",
                            success=True,
                        )
                    except Exception:
                        pass
