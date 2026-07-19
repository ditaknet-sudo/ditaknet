"""Live server activity tracker — active jobs, running checks, recent events."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from ditaknet import database as db
from ditaknet.core.system_log_service import record, redact_metadata, redact_text, uptime_seconds

VALID_JOB_TYPES = frozenset(
    {
        "discovery_scan",
        "discovery_refresh",
        "monitoring_check",
        "notification_send",
        "license_validation",
        "report_generation",
        "backup_job",
        "attendance_refresh",
        "branch_heartbeat",
        "branch_agent",
        "scheduler_job",
    }
)


class ActivityService:
    """In-memory tracker for active background work. Events persist via system_log_service."""

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._running_checks: dict[int, dict[str, Any]] = {}
        self._job_starts: dict[str, float] = {}

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _elapsed(self, job_id: str) -> int:
        started = self._job_starts.get(job_id)
        if not started:
            return 0
        return max(0, int(time.monotonic() - started))

    async def record_event(
        self,
        category: str,
        level: str,
        event_type: str,
        message: str,
        *,
        source: str = "activity",
        entity_type: str = "",
        entity_id: str | int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict | None:
        return await record(
            level,
            category,
            event_type,
            message,
            source=source,
            entity_type=entity_type,
            entity_id=entity_id,
            metadata=metadata,
        )

    async def start_job(
        self,
        job_type: str,
        *,
        target: str = "",
        total_steps: int | None = None,
        job_id: str | None = None,
        category: str = "system",
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        resolved_id = job_id or job_type
        resolved_type = job_type if job_type in VALID_JOB_TYPES else category or "scheduler_job"
        self._job_starts[resolved_id] = time.monotonic()
        job = {
            "id": resolved_id,
            "type": resolved_type,
            "category": category,
            "status": "running",
            "target": target,
            "started_at": self._now_iso(),
            "elapsed_seconds": 0,
            "current_step": "started",
            "current_target": target,
            "progress_percent": 0,
            "total_steps": total_steps,
            "message": redact_text(message),
            "metadata": redact_metadata(metadata),
        }
        self._jobs[resolved_id] = job
        return resolved_id

    async def update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        current_step: str | None = None,
        current_target: str | None = None,
        progress_percent: int | None = None,
        message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        job = self._jobs.get(job_id)
        if not job:
            return None
        if status:
            job["status"] = status
        if current_step is not None:
            job["current_step"] = current_step
        if current_target is not None:
            job["current_target"] = current_target
        if progress_percent is not None:
            job["progress_percent"] = max(0, min(100, int(progress_percent)))
        if message is not None:
            job["message"] = redact_text(message)
        if metadata:
            job["metadata"] = {**job.get("metadata", {}), **redact_metadata(metadata)}
        job["elapsed_seconds"] = self._elapsed(job_id)
        return job

    async def finish_job(
        self,
        job_id: str,
        *,
        status: str = "completed",
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        job = self._jobs.pop(job_id, None)
        self._job_starts.pop(job_id, None)
        if job:
            event_metadata: dict[str, Any] = {
                "job_type": job.get("type"),
                "target": job.get("target"),
            }
            if metadata:
                event_metadata.update(redact_metadata(metadata))
            await self.record_event(
                _category_for_job(job.get("type", "")),
                "success" if status == "completed" else "info",
                f"job_{status}",
                message or f"Job {job_id} {status}",
                entity_type="job",
                entity_id=job_id,
                metadata=event_metadata,
            )

    async def fail_job(self, job_id: str, error: str) -> None:
        job = self._jobs.pop(job_id, None)
        self._job_starts.pop(job_id, None)
        safe_error = redact_text(str(error)[:500])
        if job:
            await self.record_event(
                _category_for_job(job.get("type", "")),
                "error",
                "job_failed",
                f"Job {job_id} failed: {safe_error}",
                entity_type="job",
                entity_id=job_id,
                metadata={"job_type": job.get("type"), "error": safe_error},
            )

    def get_active_jobs(self) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        for job_id, job in self._jobs.items():
            payload = dict(job)
            payload["elapsed_seconds"] = self._elapsed(job_id)
            jobs.append(payload)
        for check in self._running_checks.values():
            jobs.append(dict(check))
        return sorted(jobs, key=lambda item: item.get("started_at") or "", reverse=True)

    async def start_check(self, service: dict, host_name: str) -> str:
        service_id = int(service["id"])
        check_id = f"check_{service_id}_{uuid.uuid4().hex[:8]}"
        self._running_checks[service_id] = {
            "id": check_id,
            "type": "monitoring_check",
            "status": "running",
            "target": f"{host_name} {service.get('target', '')}",
            "started_at": self._now_iso(),
            "elapsed_seconds": 0,
            "current_step": "executing",
            "current_target": str(service.get("target") or ""),
            "progress_percent": 0,
            "message": f"Running {service.get('check_type')} check: {service.get('name')}",
            "metadata": {
                "service_id": service_id,
                "service_name": service.get("name"),
                "host_name": host_name,
                "check_type": service.get("check_type"),
            },
        }
        return check_id

    async def finish_check(self, service_id: int, *, status: str, message: str) -> None:
        row = self._running_checks.pop(int(service_id), None)
        if row:
            row["status"] = status
            row["message"] = redact_text(message)[:300]

    def get_running_checks(self) -> list[dict[str, Any]]:
        return list(self._running_checks.values())

    def active_jobs_count(self) -> int:
        return len(self._jobs) + len(self._running_checks)

    def checks_running_count(self) -> int:
        return len(self._running_checks)

    async def get_summary(self) -> dict[str, Any]:
        from ditaknet.api.v1.system import _scheduler_payload
        from ditaknet.health import deep_health

        deep = await deep_health()
        scheduler = await _scheduler_payload()
        scans = await db.list_discovery_scans(limit=5)
        discovery_running = sum(
            1 for scan in scans if scan.get("status") in {"pending", "running"}
        )
        last_event = await db.get_last_system_log()
        return {
            "uptime_seconds": uptime_seconds(),
            "scheduler_status": "running" if scheduler.get("running") else "stopped",
            "database_status": "connected" if deep.get("database", {}).get("ok") else "error",
            "app_status": deep.get("status", "unknown"),
            "active_jobs_count": self.active_jobs_count(),
            "checks_running": self.checks_running_count(),
            "discovery_running": discovery_running,
            "errors_last_24h": await db.count_system_logs_since(hours=24, levels=["error", "critical"]),
            "warnings_last_24h": await db.count_system_logs_since(hours=24, level="warning"),
            "last_event_at": last_event.get("created_at") if last_event else None,
            "scheduler": scheduler,
        }


def _category_for_job(job_type: str) -> str:
    if job_type.startswith("discovery"):
        return "discovery"
    if job_type in {"monitoring_check", "branch_heartbeat", "branch_agent"}:
        return "monitoring"
    if job_type == "notification_send":
        return "notification"
    if job_type == "license_validation":
        return "license"
    return "system"


activity_service = ActivityService()
