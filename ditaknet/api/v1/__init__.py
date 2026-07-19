"""
DitakNet — API v1 router.

Aggregates all v1 endpoint routers under ``/api/v1``.
"""

from fastapi import APIRouter

from ditaknet.api.v1 import agents, alerts, auth, backups, checks, dashboard, devices, hosts, reports, services, system

router = APIRouter(prefix="/api/v1", tags=["v1"])

router.include_router(hosts.router)
router.include_router(services.router)
router.include_router(checks.router)
router.include_router(alerts.router)
router.include_router(dashboard.router)
router.include_router(devices.router)
router.include_router(auth.router)
router.include_router(reports.router)
router.include_router(backups.router)
router.include_router(system.router)
router.include_router(agents.router)
