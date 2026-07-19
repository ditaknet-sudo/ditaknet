"""
Shared engine references for API routes and health checks.

Engines are created in ``main.lifespan`` and injected here so routes stay thin
and tests can swap mocks via ``set_engines`` without restarting FastAPI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ditaknet.core.alert_engine import AlertEngine
    from ditaknet.core.scheduler import Scheduler
    from ditaknet.core.state_engine import StateEngine

# These are set during app startup in main.py
_scheduler: Scheduler | None = None
_state_engine: StateEngine | None = None
_alert_engine: AlertEngine | None = None


def set_engines(
    scheduler: Scheduler,
    state_engine: StateEngine,
    alert_engine: AlertEngine,
) -> None:
    """Wire up engine instances (called once during app startup)."""
    global _scheduler, _state_engine, _alert_engine
    _scheduler = scheduler
    _state_engine = state_engine
    _alert_engine = alert_engine


def get_scheduler() -> Scheduler:
    if _scheduler is None:
        raise RuntimeError("Scheduler not initialised")
    return _scheduler


def get_state_engine() -> StateEngine:
    if _state_engine is None:
        raise RuntimeError("State engine not initialised")
    return _state_engine


def get_alert_engine() -> AlertEngine:
    if _alert_engine is None:
        raise RuntimeError("Alert engine not initialised")
    return _alert_engine
