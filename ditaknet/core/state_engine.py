"""
Per-service health state machine (OK / WARNING / CRITICAL).

SOFT transitions (first failures) update state but may suppress notifications;
HARD transitions fire after ``settings.alert_failure_threshold`` consecutive failures.
Recovery to OK clears failure count and may emit a recovery alert — see ``AlertEngine``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Optional

from loguru import logger

from ditaknet.config import settings
from ditaknet.core.checks.base import CheckResponse
from ditaknet.models import ServiceState


@dataclass(frozen=True)
class StateTransition:
    """A service state transition with alerting metadata.

    The object intentionally unpacks like the old ``(old_state, new_state)``
    tuple so existing callers keep working while newer code can inspect
    soft/hard/recovery details.
    """

    old_state: ServiceState
    new_state: ServiceState
    state_type: str
    failure_count: int = 0
    recovered_from_hard: bool = False

    def __iter__(self) -> Iterator[ServiceState]:
        yield self.old_state
        yield self.new_state

    @property
    def is_soft(self) -> bool:
        return self.state_type == "soft"

    @property
    def is_hard(self) -> bool:
        return self.state_type == "hard"

    @property
    def is_recovery(self) -> bool:
        return self.state_type == "recovery"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, StateTransition):
            return (
                self.old_state == other.old_state
                and self.new_state == other.new_state
                and self.state_type == other.state_type
                and self.failure_count == other.failure_count
                and self.recovered_from_hard == other.recovered_from_hard
            )
        if isinstance(other, tuple) and len(other) == 2:
            return (self.old_state, self.new_state) == other
        return False


class StateEngine:
    """Per-service state machine with configurable thresholds."""

    def __init__(
        self,
        warning_threshold: int | None = None,
        critical_threshold: int | None = None,
    ):
        warning = settings.warning_threshold if warning_threshold is None else warning_threshold
        critical = settings.critical_threshold if critical_threshold is None else critical_threshold
        self.warning_threshold = max(1, warning)
        self.critical_threshold = max(self.warning_threshold, critical)

        # service_id → consecutive failure count
        self._failure_counts: dict[int, int] = {}
        # service_id → current state
        self._states: dict[int, ServiceState] = {}

    # ── Public API ────────────────────────────────────────

    def get_state(self, service_id: int) -> ServiceState:
        """Return the current state for a service."""
        return self._states.get(service_id, ServiceState.UNKNOWN)

    def set_state(self, service_id: int, state: ServiceState) -> None:
        """Force-set a service state (used on startup sync)."""
        self._states[service_id] = state
        if state == ServiceState.OK:
            self._failure_counts[service_id] = 0
        elif state == ServiceState.WARNING:
            self._failure_counts[service_id] = self.warning_threshold
        elif state == ServiceState.CRITICAL:
            self._failure_counts[service_id] = self.critical_threshold

    def process_result(
        self,
        service_id: int,
        check_response: CheckResponse,
    ) -> Optional[StateTransition]:
        """Evaluate a check result and return a state transition if one occurs.

        Returns
        -------
        ``StateTransition`` if the state changed, or ``None`` if unchanged.
        """
        current = self._states.get(service_id, ServiceState.UNKNOWN)

        if check_response.success:
            self._failure_counts[service_id] = 0
            new_state = ServiceState.OK
            state_type = "recovery" if current in self._failing_states() else "stable"
            recovered_from_hard = current == ServiceState.CRITICAL
        else:
            count = self._failure_counts.get(service_id, 0) + 1
            self._failure_counts[service_id] = count
            if count < self.warning_threshold:
                logger.debug(
                    "Soft failure for service {} ({}/{} before warning)",
                    service_id,
                    count,
                    self.warning_threshold,
                )
                return None
            new_state = self._determine_failure_state(count)
            state_type = "hard" if new_state == ServiceState.CRITICAL else "soft"
            recovered_from_hard = False

        if new_state != current:
            self._states[service_id] = new_state
            logger.info(
                "State transition for service {}: {} → {}",
                service_id,
                current.value,
                new_state.value,
            )
            return StateTransition(
                old_state=current,
                new_state=new_state,
                state_type=state_type,
                failure_count=self._failure_counts.get(service_id, 0),
                recovered_from_hard=recovered_from_hard,
            )

        return None

    def get_failure_count(self, service_id: int) -> int:
        """Return the current consecutive failure count for a service."""
        return self._failure_counts.get(service_id, 0)

    def remove_service(self, service_id: int) -> None:
        """Clean up tracking data for a removed service."""
        self._failure_counts.pop(service_id, None)
        self._states.pop(service_id, None)

    def reset(self) -> None:
        """Clear all tracked state (used in tests)."""
        self._failure_counts.clear()
        self._states.clear()

    # ── Internals ─────────────────────────────────────────

    def _determine_failure_state(self, consecutive_failures: int) -> ServiceState:
        """Map consecutive failure count to a state."""
        if consecutive_failures >= self.critical_threshold:
            return ServiceState.CRITICAL
        elif consecutive_failures >= self.warning_threshold:
            return ServiceState.WARNING
        return ServiceState.OK

    @staticmethod
    def _failing_states() -> set[ServiceState]:
        return {ServiceState.WARNING, ServiceState.CRITICAL}
