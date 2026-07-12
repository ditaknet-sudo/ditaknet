"""Production-safe logging — rotating files under LOG_DIR, secrets masked in summary."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from ditaknet.config import settings


def configure_logging() -> Path | None:
    """Configure console and optional rotating file logs. Returns log file path."""
    logger.remove()
    level = settings.log_level.upper()
    logger.add(
        sys.stderr,
        level=level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{function}:{line} - {message}",
        enqueue=True,
    )

    log_dir = settings.log_dir_path
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "ditaknet.log"
    logger.add(
        str(log_file),
        level=level,
        rotation="10 MB",
        retention="14 days",
        compression="zip",
        enqueue=True,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{function}:{line} - {message}",
    )
    return log_file


def log_startup_summary() -> None:
    """Log config snapshot for support/debug without exposing secret values."""
    logger.info("DitakNet startup configuration summary")
    logger.info("  app_env={}", settings.app_env)
    logger.info("  app_version={}", settings.app_version)
    logger.info("  database_path={}", settings.db_path)
    logger.info("  data_dir={}", settings.data_dir_path)
    logger.info("  backup_dir={}", settings.backup_dir_path)
    logger.info("  log_dir={}", settings.log_dir_path)
    logger.info("  scheduler_enabled={}", settings.scheduler_enabled)
    logger.info("  telegram_enabled={}", settings.telegram_enabled)
    logger.info("  admin_username={}", settings.admin_username)
    logger.info("  secret_key_configured={}", bool(settings.effective_secret_key))
