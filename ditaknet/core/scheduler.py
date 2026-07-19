"""
APScheduler wrapper for recurring service checks.

Job IDs are deterministic per service (``check_service_{id}``) so reschedule
does not create duplicates — ``_add_job`` removes an existing job first.

``_execute_once`` catches all check exceptions and returns a failed
``CheckResponse`` so one bad service never crashes the scheduler thread.
Plugins may register extra check types into ``CHECK_REGISTRY`` at startup.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from ditaknet import database as db
from ditaknet.config import settings
from ditaknet.core.activity_service import activity_service
from ditaknet.core.checks.base import BaseCheck, CheckResponse
from ditaknet.core.checks.http import HttpCheck
from ditaknet.core.checks.ping import PingCheck
from ditaknet.core.checks.tcp import TcpCheck
from ditaknet.models import ServiceState

if TYPE_CHECKING:
    from ditaknet.core.alert_engine import AlertEngine
    from ditaknet.core.state_engine import StateEngine, StateTransition


CHECK_REGISTRY: dict[str, type[BaseCheck]] = {
    "ping": PingCheck,
    "http": HttpCheck,
    "tcp": TcpCheck,
}


class Scheduler:
    """Manages recurring service checks via APScheduler."""

    def __init__(
        self,
        state_engine: StateEngine,
        alert_engine: AlertEngine,
        *,
        max_check_attempts: int = 2,
        retry_backoff_seconds: float = 0.2,
    ):
        self.state_engine = state_engine
        self.alert_engine = alert_engine
        self.max_check_attempts = max(1, max_check_attempts)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self._scheduler = AsyncIOScheduler(
            job_defaults={
                "coalesce": True,  # skip backlog if server was busy
                "max_instances": 1,  # never overlap the same service check
                "misfire_grace_time": 30,
            }
        )

    async def start(self) -> None:
        """Load all enabled services from DB and start the scheduler."""
        services = await db.list_services()
        count = 0
        for svc in services:
            if svc["enabled"]:
                self._add_job(svc)
                await activity_service.record_event(
                    "scheduler",
                    "info",
                    "scheduled_job_registered",
                    f"Registered check job for {svc.get('name')}",
                    metadata={
                        "service_id": svc.get("id"),
                        "interval_seconds": svc.get("interval_seconds"),
                        "check_type": svc.get("check_type"),
                        "target": str(svc.get("target") or ""),
                    },
                    source="scheduler",
                )
                state_val = svc.get("current_state", "unknown")
                self.state_engine.set_state(
                    svc["id"],
                    ServiceState(state_val),
                )
                count += 1

        self._scheduler.start()
        self._scheduler.add_job(
            self._check_agent_heartbeats,
            trigger="interval",
            seconds=settings.agent_heartbeat_check_interval_seconds,
            id="agent_heartbeat_monitor",
            name="agent:heartbeat_monitor",
            replace_existing=True,
        )
        if settings.result_retention_days > 0 or settings.alert_retention_days > 0 or settings.metric_retention_days > 0:
            self._scheduler.add_job(
                self._run_retention_cleanup,
                trigger="interval",
                hours=24,
                id="retention_cleanup",
                name="system:retention_cleanup",
                replace_existing=True,
            )
        if settings.discovery_enabled:
            from ditaknet.discovery.refresh import ensure_refresh_defaults, refresh_interval_minutes

            await ensure_refresh_defaults()
            minutes = await refresh_interval_minutes()
            self._scheduler.add_job(
                self._run_discovery_refresh,
                trigger="interval",
                minutes=max(1, minutes),
                id="discovery_refresh",
                name="discovery:periodic_refresh",
                replace_existing=True,
            )
        self._scheduler.add_job(
            self._run_system_health_checks,
            trigger="interval",
            minutes=15,
            id="system_health_checks",
            name="system:health_checks",
            replace_existing=True,
        )
        if settings.auto_backup_enabled:
            self._scheduler.add_job(
                self._run_weekly_automatic_backup,
                trigger=CronTrigger(day_of_week="sun", hour=3, minute=0),
                id="weekly_automatic_backup",
                name="system:weekly_automatic_backup",
                replace_existing=True,
            )
        logger.info("Scheduler started with {} active jobs", count)
        await activity_service.record_event(
            "scheduler",
            "success",
            "scheduler_started",
            f"Scheduler started with {count} active service jobs",
            metadata={"active_service_jobs": count},
            source="scheduler",
        )
        try:
            from ditaknet.core.system_log_service import record

            await record(
                "info",
                "monitoring",
                "scheduler_started",
                f"Scheduler started with {count} active jobs",
                source="scheduler",
            )
        except Exception:
            pass

    async def stop(self) -> None:
        """Gracefully shut down the scheduler."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")
            await activity_service.record_event(
                "scheduler",
                "warning",
                "scheduler_stopped",
                "Scheduler stopped",
                source="scheduler",
            )
            try:
                from ditaknet.core.system_log_service import record

                await record(
                    "info",
                    "monitoring",
                    "scheduler_stopped",
                    "Scheduler stopped",
                    source="scheduler",
                )
            except Exception:
                pass

    async def reload_services(self) -> None:
        """Remove service check jobs and reload from the current database."""
        if not self._scheduler.running:
            await self.start()
            return
        for job in list(self._scheduler.get_jobs()):
            if str(job.id).startswith("check_service_"):
                self._scheduler.remove_job(job.id)
        self.state_engine.reset()
        count = 0
        for svc in await db.list_services():
            if svc.get("enabled"):
                self._add_job(svc)
                state_val = svc.get("current_state", "unknown")
                self.state_engine.set_state(svc["id"], ServiceState(state_val))
                count += 1
        logger.info("Scheduler reloaded with {} service jobs", count)

    def add_service(self, service: dict) -> None:
        """Schedule a check job for a newly created/enabled service."""
        self._add_job(service)
        logger.info(
            "Added check job for service {} (every {}s)",
            service["id"],
            service["interval_seconds"],
        )

    def remove_service(self, service_id: int) -> None:
        """Remove a scheduled check job."""
        job_id = self._job_id(service_id)
        if self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)
            self.state_engine.remove_service(service_id)
            logger.info("Removed check job for service {}", service_id)

    def reschedule_service(self, service: dict) -> None:
        """Update the interval for an existing service job.

        Remove-then-add avoids duplicate jobs when interval or enabled flag changes.
        """
        self.remove_service(service["id"])
        if service.get("enabled", True):
            self._add_job(service)

    async def trigger_check(self, service_id: int) -> Optional[dict]:
        """Run a check immediately for a specific service."""
        svc = await db.get_service(service_id)
        if not svc:
            return None
        return await self._run_check(svc)

    async def status(self) -> dict:
        """Return scheduler runtime status without exposing job callables."""
        jobs = self._scheduler.get_jobs() if self._scheduler else []
        services = await db.list_services()
        return {
            "running": bool(getattr(self._scheduler, "running", False)),
            "job_count": len(jobs),
            "jobs": [
                {
                    "id": job.id,
                    "name": job.name,
                    "next_run_time": (
                        job.next_run_time.isoformat()
                        if getattr(job, "next_run_time", None)
                        else None
                    ),
                }
                for job in jobs
            ],
            "enabled_services": sum(1 for service in services if service.get("enabled")),
            "total_services": len(services),
        }

    def _add_job(self, service: dict) -> None:
        """Create an APScheduler interval job for a service."""
        job_id = self._job_id(service["id"])
        if self._scheduler.get_job(job_id):
            self._scheduler.remove_job(job_id)
        self._scheduler.add_job(
            self._run_check,
            trigger="interval",
            seconds=service["interval_seconds"],
            id=job_id,
            args=[service],
            name=f"check:{service['name']}",
        )

    async def _run_check(self, service: dict) -> dict:
        """Execute a single check and process the result."""
        check_type = service["check_type"]
        check_cls = CHECK_REGISTRY.get(check_type)
        if not check_cls:
            logger.error("Unknown check type '{}' for service {}", check_type, service["id"])
            return {}

        host = await db.get_host(service["host_id"])
        host_name = host["name"] if host else "Unknown"
        try:
            from ditaknet.core.system_log_service import record_check_started

            await record_check_started(service, host_name)
        except Exception:
            pass
        try:
            from ditaknet.core.activity_service import activity_service

            await activity_service.start_check(service, host_name)
        except Exception:
            pass

        checker = check_cls()
        extra_kwargs = self._extra_check_kwargs(check_type, service)
        response = await self._execute_with_retries(checker, service, extra_kwargs)

        transition = self.state_engine.process_result(service["id"], response)
        status = self._result_status(service["id"], response, transition)

        try:
            result = await db.insert_check_result(
                service_id=service["id"],
                status=status.value,
                response_time_ms=response.response_time_ms,
                message=response.message,
            )
        except Exception as exc:
            logger.error("Failed to store check result for service {}: {}", service["id"], exc)
            result = {}

        if transition is not None:
            old_state, new_state = transition
            try:
                await db.update_service(service["id"], current_state=new_state.value)
            except Exception as exc:
                logger.error("Failed to update service state: {}", exc)

            try:
                from ditaknet.core.system_log_service import record_state_change

                await record_state_change(
                    service["id"],
                    service["name"],
                    host_name,
                    old_state.value,
                    new_state.value,
                )
            except Exception:
                pass

            await self.alert_engine.process_state_change(
                service_id=service["id"],
                service_name=service["name"],
                host_name=host_name,
                old_state=old_state,
                new_state=new_state,
                check_response=response,
                state_type=getattr(transition, "state_type", None),
            )

        try:
            from ditaknet.core.system_log_service import record_check_completed

            await record_check_completed(
                service,
                host_name,
                status=status.value,
                response_time_ms=response.response_time_ms,
                message=response.message,
            )
        except Exception:
            pass
        try:
            from ditaknet.core.activity_service import activity_service

            await activity_service.finish_check(
                int(service["id"]),
                status=status.value,
                message=response.message or status.value,
            )
        except Exception:
            pass

        return result

    async def _execute_with_retries(
        self,
        checker: BaseCheck,
        service: dict,
        extra_kwargs: dict,
    ) -> CheckResponse:
        attempts: list[CheckResponse] = []
        max_attempts = self._max_attempts_for_service(service)
        for attempt in range(1, max_attempts + 1):
            response = await self._execute_once(checker, service, extra_kwargs)
            response.extra = dict(response.extra or {})
            response.extra["attempt"] = attempt
            response.extra["max_attempts"] = max_attempts
            attempts.append(response)

            retryable = self._is_retryable(response)
            if response.success or not retryable or attempt == max_attempts:
                break

            await self._sleep_before_retry(attempt)

        final = attempts[-1]
        final.extra["attempts"] = len(attempts)
        final.extra["retry_exhausted"] = (
            not final.success
            and self._is_retryable(final)
            and len(attempts) == max_attempts
        )
        if len(attempts) > 1:
            final.message = f"{final.message} (after {len(attempts)} attempts)"
        return final

    async def _execute_once(
        self,
        checker: BaseCheck,
        service: dict,
        extra_kwargs: dict,
    ) -> CheckResponse:
        start = time.perf_counter()
        try:
            return await checker.execute(
                target=service["target"],
                port=service.get("port"),
                timeout=service.get("timeout_seconds", settings.default_check_timeout),
                **extra_kwargs,
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            # Never propagate — APScheduler would disable the job on uncaught errors.
            logger.warning(
                "{} check raised for service {}: {}",
                service["check_type"],
                service["id"],
                exc,
            )
            return CheckResponse(
                success=False,
                response_time_ms=elapsed_ms,
                message=f"{service['check_type']} check error: {exc}",
                extra={"error_type": exc.__class__.__name__, "retryable": True},
            )

    async def _sleep_before_retry(self, attempt: int) -> None:
        if self.retry_backoff_seconds == 0:
            return
        delay = min(self.retry_backoff_seconds * (2 ** (attempt - 1)), 5.0)
        await asyncio.sleep(delay)

    @staticmethod
    def _extra_check_kwargs(check_type: str, service: dict) -> dict:
        extra_kwargs = {}
        if check_type == "http" and "expected_status_code" in service:
            extra_kwargs["expected_status_code"] = service.get("expected_status_code")
        return extra_kwargs

    @staticmethod
    def _is_retryable(response: CheckResponse) -> bool:
        if response.success:
            return False
        return bool(response.extra.get("retryable", True))

    def _max_attempts_for_service(self, service: dict) -> int:
        raw = service.get("max_attempts")
        if raw is None:
            retry_count = service.get("retry_count")
            if retry_count is None:
                raw = self.max_check_attempts
            else:
                try:
                    raw = int(retry_count) + 1
                except (TypeError, ValueError):
                    return self.max_check_attempts
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return self.max_check_attempts

    def _result_status(
        self,
        service_id: int,
        response: CheckResponse,
        transition: StateTransition | None,
    ) -> ServiceState:
        if response.success:
            return ServiceState.OK
        if transition is not None:
            return transition.new_state

        current = self.state_engine.get_state(service_id)
        if current in {ServiceState.WARNING, ServiceState.CRITICAL}:
            return current
        return ServiceState.WARNING

    @staticmethod
    def _job_id(service_id: int) -> str:
        """Deterministic job ID from service ID."""
        return f"check_service_{service_id}"

    async def _check_agent_heartbeats(self) -> None:
        """Detect agents with missing heartbeats and raise alerts."""
        from ditaknet.core.metrics_engine import check_missing_heartbeats

        job_id = await activity_service.start_job(
            "branch_agent_heartbeat",
            target="agents",
            category="branch_agent",
            message="Checking agent heartbeats",
        )
        try:
            count = await check_missing_heartbeats()
            if count:
                logger.info("Marked {} agent(s) offline due to missing heartbeat", count)
            await activity_service.finish_job(
                job_id,
                status="completed",
                message=f"Heartbeat check completed; offline agents: {count}",
            )
        except Exception as exc:
            logger.error("Agent heartbeat monitor failed: {}", exc)
            await activity_service.fail_job(job_id, str(exc))

    async def _run_discovery_refresh(self) -> None:
        """Periodic network discovery refresh for monitored subnets."""
        job_id = await activity_service.start_job(
            "scheduled_refresh",
            target="discovery",
            category="scheduler",
            message="Running scheduled discovery refresh",
        )
        try:
            from ditaknet.discovery.refresh import run_discovery_refresh

            result = await run_discovery_refresh()
            if result.get("started"):
                logger.info("Discovery refresh started scan {}", result.get("scan_id"))
            await activity_service.finish_job(
                job_id,
                status="completed",
                message=f"Discovery refresh completed; started={bool(result.get('started'))}",
                metadata=result,
            )
        except Exception as exc:
            logger.error("Discovery refresh failed: {}", exc)
            await activity_service.fail_job(job_id, str(exc))

    async def _run_system_health_checks(self) -> None:
        from ditaknet.core.system_health_monitor import run_system_health_checks

        try:
            await run_system_health_checks()
        except Exception as exc:
            logger.warning("System health check job failed: {}", exc)

    async def _run_retention_cleanup(self) -> None:
        """Purge old results, alerts, and metrics according to retention settings."""
        job_id = await activity_service.start_job(
            "retention_cleanup",
            target="database",
            category="scheduler",
            message="Running retention cleanup",
        )
        try:
            removed = await db.purge_old_data()
            if removed:
                logger.info("Retention cleanup removed {} records", removed)
            await activity_service.finish_job(
                job_id,
                status="completed",
                message=f"Retention cleanup removed {removed or 0} records",
            )
        except Exception as exc:
            logger.error("Retention cleanup failed: {}", exc)
            await activity_service.fail_job(job_id, str(exc))

    async def _run_weekly_automatic_backup(self) -> None:
        from ditaknet.core.backup import run_weekly_automatic_backup

        job_id = await activity_service.start_job(
            "weekly_automatic_backup",
            target="backups",
            category="scheduler",
            message="Running weekly automatic backup",
        )
        try:
            result = await run_weekly_automatic_backup()
            if result.get("skipped"):
                await activity_service.finish_job(
                    job_id,
                    status="completed",
                    message="Weekly automatic backup skipped (disabled)",
                    metadata=result,
                )
                return
            await activity_service.finish_job(
                job_id,
                status="completed",
                message=f"Weekly automatic backup created: {result.get('filename')}",
                metadata=result,
            )
        except Exception as exc:
            logger.error("Weekly automatic backup failed: {}", exc)
            await activity_service.fail_job(job_id, str(exc))
