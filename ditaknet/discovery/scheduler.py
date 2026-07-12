"""Background discovery scan orchestration with cancellation and progress."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from loguru import logger

from ditaknet import database as db
from ditaknet.discovery.diagnostics import build_scan_diagnostics
from ditaknet.discovery.inventory import upsert_discovered_device
from ditaknet.discovery.scanner import NetworkScanner, ScanProgress
from ditaknet.discovery.subnet import iter_hosts, normalize_subnets
from ditaknet.resilience import create_background_task


class DiscoveryScheduler:
    """Manages in-flight discovery scans (one asyncio task per scan)."""

    def __init__(self) -> None:
        self._tasks: dict[int, asyncio.Task] = {}
        self._cancel_flags: dict[int, bool] = {}
        self._progress: dict[int, ScanProgress] = {}

    def get_progress(self, scan_id: int) -> dict[str, Any]:
        prog = self._progress.get(scan_id)
        if not prog:
            return {"percent": 0, "scanned": 0, "total": 0, "found": 0}
        return {
            "percent": prog.percent,
            "scanned": prog.scanned,
            "total": prog.total,
            "found": prog.found,
            "failed_probes": prog.failed_probes,
            "current_ip": prog.current_ip,
            "current_subnet": prog.current_subnet,
            "stage": prog.stage,
            "stage_message": prog.stage_message,
            "elapsed_seconds": prog.elapsed_seconds,
            "cancelled": prog.cancelled,
        }

    def _progress_fields(self, progress: ScanProgress) -> dict[str, Any]:
        meta = {
            "gateway_ip": progress.gateway_ip,
            "gateway_checked": progress.gateway_checked,
            "gateway_reachable": progress.gateway_reachable,
            "container_limited": progress.container_limited,
            "permission_errors": list(progress.permission_errors),
        }
        return {
            "progress_percent": progress.percent,
            "scanned_hosts": progress.scanned,
            "total_hosts": progress.total,
            "found_count": progress.found,
            "failed_probe_count": progress.failed_probes,
            "current_ip": progress.current_ip,
            "current_subnet": progress.current_subnet,
            "current_stage": progress.stage,
            "stage_message": progress.stage_message,
            "elapsed_seconds": progress.elapsed_seconds,
            "probe_methods_json": json.dumps(progress.probe_methods),
            "diagnostic_meta_json": json.dumps(meta),
            "permission_errors_json": json.dumps(progress.permission_errors),
        }

    async def start_scan(
        self,
        scan_id: int,
        subnets: list[str],
        profile: str = "normal",
    ) -> None:
        if scan_id in self._tasks and not self._tasks[scan_id].done():
            raise RuntimeError(f"Scan {scan_id} already running")
        self._cancel_flags[scan_id] = False
        self._progress[scan_id] = ScanProgress()
        self._tasks[scan_id] = create_background_task(
            self._run_scan(scan_id, subnets, profile),
            name=f"discovery_scan_{scan_id}",
        )

    async def cancel_scan(self, scan_id: int) -> bool:
        self._cancel_flags[scan_id] = True
        task = self._tasks.get(scan_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await db.update_discovery_scan(scan_id, status="cancelled")
        return True

    async def _run_scan(self, scan_id: int, subnets: list[str], profile: str) -> None:
        progress = self._progress[scan_id]
        try:
            normalized = normalize_subnets(subnets)
            total_hosts = sum(1 for cidr in normalized for _ in iter_hosts(cidr))
            progress.total = total_hosts
            progress.probe_methods = ["icmp_ping", "tcp_connect"]
            logger.info(
                "Discovery scan {} starting: request_id={} subnets={} profile={} probe_methods={}",
                scan_id,
                (await db.get_discovery_scan(scan_id) or {}).get("request_id") or "",
                normalized,
                profile,
                progress.probe_methods,
            )
            await db.update_discovery_scan(
                scan_id,
                status="running",
                subnets_json=json.dumps(normalized),
                **self._progress_fields(progress),
            )
            scanner = NetworkScanner(profile=profile)  # type: ignore[arg-type]

            async def persist_progress(prog: ScanProgress) -> None:
                await db.update_discovery_scan(scan_id, **self._progress_fields(prog))

            async def persist(host) -> None:
                await upsert_discovered_device(scan_id, host)
                subnet = normalized[0] if len(normalized) == 1 else ""
                if not subnet and normalized:
                    from ditaknet.discovery.subnet import address_in_subnet

                    for cidr in normalized:
                        if address_in_subnet(host.ip_address, cidr):
                            subnet = cidr
                            break
                if subnet:
                    from ditaknet.discovery import store as discovery_store

                    evidence = host.raw_metadata.get("evidence") or []
                    await discovery_store.sync_discovery_inventory_device(
                        subnet=subnet,
                        scan_id=scan_id,
                        ip_address=host.ip_address,
                        mac_address=host.mac_address,
                        hostname=host.hostname,
                        vendor=host.vendor,
                        detected_type=host.detected_type,
                        confidence=host.confidence,
                        open_ports=json.dumps(host.open_ports),
                        discovery_source=host.discovery_source,
                        evidence_json=json.dumps(evidence),
                    )
                await db.update_discovery_scan(
                    scan_id,
                    **self._progress_fields(progress),
                )

            await scanner.scan_subnets(
                normalized,
                progress,
                on_host=persist,
                on_progress=persist_progress,
                should_cancel=lambda: self._cancel_flags.get(scan_id, False),
            )
            status = "cancelled" if progress.cancelled else "completed"
            progress.stage = "saving_results"
            progress.stage_message = "Saving discovery scan results"
            base_scan = (await db.get_discovery_scan(scan_id)) or {}
            diagnostics = build_scan_diagnostics({**base_scan, **self._progress_fields(progress)}, self.get_progress(scan_id))
            await db.update_discovery_scan(
                scan_id,
                status=status,
                progress_percent=100 if status == "completed" else progress.percent,
                diagnostics_json=json.dumps(diagnostics),
                **{
                    k: v
                    for k, v in self._progress_fields(progress).items()
                    if k != "progress_percent"
                },
            )
            logger.info(
                "Discovery scan {} finished: status={} subnets={} scanned={} total={} found={} failed_probes={} diagnostics={}",
                scan_id,
                status,
                normalized,
                progress.scanned,
                progress.total,
                progress.found,
                progress.failed_probes,
                [d.get("code") for d in diagnostics],
            )
            if status == "completed" and len(normalized) == 1:
                from ditaknet.discovery import store as discovery_store

                seen_ips = {
                    str(d.get("ip_address") or "")
                    for d in await db.list_discovered_devices(scan_id=scan_id, hide_demo=True)
                }
                await discovery_store.mark_inventory_devices_missing(
                    normalized[0], seen_ips, scan_id
                )
            if status == "completed":
                from ditaknet.api.deps import get_scheduler
                from ditaknet.discovery.auto_import import auto_import_scan

                try:
                    scheduler = get_scheduler()
                except RuntimeError:
                    scheduler = None
                await auto_import_scan(scan_id, scheduler=scheduler)
        except Exception as exc:
            base_scan = (await db.get_discovery_scan(scan_id)) or {}
            logger.bind(
                scan_id=scan_id,
                request_id=base_scan.get("request_id") or "",
                subnets=subnets,
                profile=profile,
                found_count=progress.found,
                failed_probe_count=progress.failed_probes,
                probe_methods=progress.probe_methods,
                permission_errors=progress.permission_errors,
            ).exception("Discovery scan {} failed: {}", scan_id, exc)
            diagnostics = build_scan_diagnostics(
                {
                    **base_scan,
                    **self._progress_fields(progress),
                    "status": "failed",
                    "error_message": str(exc)[:500],
                },
                self.get_progress(scan_id),
            )
            await db.update_discovery_scan(
                scan_id,
                status="failed",
                error_message=str(exc)[:500],
                diagnostics_json=json.dumps(diagnostics),
                **self._progress_fields(progress),
            )
        finally:
            self._tasks.pop(scan_id, None)
            self._cancel_flags.pop(scan_id, None)


discovery_scheduler = DiscoveryScheduler()
