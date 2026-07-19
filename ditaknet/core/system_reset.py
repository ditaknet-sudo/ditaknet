"""Factory reset — wipe SQLite data and return to first-run setup."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from ditaknet import database as db
from ditaknet.core.licensing import license_service

FACTORY_RESET_CONFIRMATION = "RESET DITAKNET"


def _sqlite_sidecars(db_path: Path) -> list[Path]:
    paths = [db_path]
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{db_path}{suffix}")
        if sidecar.is_file():
            paths.append(sidecar)
    return paths


async def factory_reset_to_setup(
    *,
    actor: str,
    ip_address: str = "",
    confirmation: str,
) -> None:
    """Delete the SQLite database and reinitialize an empty schema.

    Requires exact confirmation text ``RESET DITAKNET``.
    Monitoring data, users (DB-stored admin), licenses, and setup state are removed.
    New admin credentials must be created during first-run setup after reset.
    """
    confirmed = confirmation.strip()
    if confirmed != FACTORY_RESET_CONFIRMATION:
        raise ValueError("Confirmation text does not match.")

    try:
        await db.create_audit_log(
            "system.factory_reset",
            actor=actor,
            resource="system",
            detail="factory_reset_to_setup",
            ip_address=ip_address,
        )
    except Exception as exc:
        logger.warning("Pre-reset audit log failed: {}", exc)

    db_path = db.get_db_path()

    await db.close_db()

    recreated = False
    try:
        for path in _sqlite_sidecars(db_path):
            path.unlink(missing_ok=True)
            logger.info("Removed {}", path)
        recreated = True
    except OSError as exc:
        logger.warning("Could not delete database files ({}); wiping in place", exc)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    await db.init_db(str(db_path))
    if not recreated:
        await db.wipe_for_factory_reset()
    await license_service.ensure_default_license()

    try:
        from ditaknet.api.deps import get_alert_engine, get_scheduler

        get_alert_engine().clear_runtime_state()
        await get_scheduler().reload_services()
    except RuntimeError as exc:
        logger.debug("Skipping engine reload after reset: {}", exc)

    logger.info("Factory reset complete — setup wizard required")
