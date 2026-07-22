"""
DitakNet configuration (pydantic-settings).

Important env groups:
  - ``APP_*`` — runtime identity
  - ``DATA_DIR``, ``BACKUP_DIR``, ``LOG_DIR`` — TrueNAS/Docker mount targets
  - ``DATABASE_URL`` — ``sqlite:////app/data/ditaknet.db`` in containers
  - Retention ``*_RETENTION_DAYS`` — consumed by scheduled purge job

Passwords are stored as database hashes. If no external session secret is
supplied, a persistent runtime signing key is generated under ``DATA_DIR``.
Secrets are never logged; see ``logging_config.log_startup_summary``.
PostgreSQL is not wired yet — non-SQLite ``DATABASE_URL`` values fail fast in production.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _sqlite_path_from_url(database_url: str) -> str | None:
    """Parse sqlite:/// or sqlite://// paths from DATABASE_URL."""
    if not database_url:
        return None
    if database_url.startswith("sqlite:"):
        parsed = urlparse(database_url)
        if parsed.path:
            # sqlite:////app/data/db -> /app/data/db on Unix
            path = parsed.path
            if database_url.startswith("sqlite:////"):
                path = "/" + path.lstrip("/")
            return path
    return None


def _load_or_create_runtime_secret(data_dir: str) -> str:
    """Keep the session signing key outside environment/config files."""
    secret_path = Path(data_dir).expanduser().resolve() / ".session_secret"
    if secret_path.is_file():
        value = secret_path.read_text(encoding="utf-8").strip()
        if len(value) >= 32:
            return value

    secret_path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_urlsafe(48)
    temporary_path = secret_path.with_suffix(".tmp")
    temporary_path.write_text(value, encoding="utf-8")
    try:
        temporary_path.chmod(0o600)
    except OSError:
        pass
    temporary_path.replace(secret_path)
    return value


class Settings(BaseSettings):
    """Application settings with env-var loading."""

    model_config = SettingsConfigDict(
        env_file="config/runtime.env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ───────────────────────────────────────
    app_name: str = Field(default="DitakNet", validation_alias="APP_NAME")
    app_display_name: str = Field(
        default="DitakNet", validation_alias="APP_DISPLAY_NAME"
    )
    app_brand_name: str = Field(default="DitakNet", validation_alias="APP_BRAND_NAME")
    app_brand_name_hy: str = Field(
        default="ԴիտակՆեթ", validation_alias="APP_BRAND_NAME_HY"
    )
    app_env: str = Field(default="production", validation_alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", validation_alias="APP_HOST")
    app_port: int = Field(default=5833, validation_alias="APP_PORT")
    app_base_url: str = Field(
        default="http://localhost:5833", validation_alias="APP_BASE_URL"
    )
    app_version: str = Field(default="2.0.1", validation_alias="APP_VERSION")
    build_commit: str = Field(
        default="",
        validation_alias=AliasChoices("BUILD_COMMIT", "APP_BUILD_COMMIT", "GIT_COMMIT"),
    )
    build_date: str = Field(
        default="",
        validation_alias=AliasChoices("BUILD_DATE", "APP_BUILD_DATE"),
    )
    image_tag: str = Field(
        default="", validation_alias=AliasChoices("IMAGE_TAG", "APP_IMAGE_TAG")
    )
    github_repository: str = Field(default="", validation_alias="GITHUB_REPOSITORY")
    ghcr_image: str = Field(default="", validation_alias="GHCR_IMAGE")
    app_build_date: str = Field(default="", validation_alias="APP_BUILD_DATE")
    app_deployment_mode: str = Field(default="", validation_alias="APP_DEPLOYMENT_MODE")

    # ── About / Support (optional — empty fields hidden in UI) ──
    app_author_name: str = Field(default="DitakNet", validation_alias="APP_AUTHOR_NAME")
    app_author_website: str = Field(default="", validation_alias="APP_AUTHOR_WEBSITE")
    app_support_email: str = Field(default="", validation_alias="APP_SUPPORT_EMAIL")
    app_support_phone: str = Field(default="", validation_alias="APP_SUPPORT_PHONE")
    app_support_telegram: str = Field(
        default="", validation_alias="APP_SUPPORT_TELEGRAM"
    )
    app_support_url: str = Field(default="", validation_alias="APP_SUPPORT_URL")
    app_documentation_url: str = Field(
        default="", validation_alias="APP_DOCUMENTATION_URL"
    )

    # Release/update metadata. Never auto-applies; notify-only.
    # Prefer DITAKNET_UPDATE_* names; APP_UPDATE_* aliases remain for compatibility.
    app_update_check_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "DITAKNET_UPDATE_CHECK_ENABLED",
            "APP_UPDATE_CHECK_ENABLED",
        ),
    )
    app_update_channel: str = Field(
        default="stable",
        validation_alias=AliasChoices(
            "DITAKNET_UPDATE_CHANNEL",
            "APP_UPDATE_CHANNEL",
        ),
    )
    app_update_check_interval_hours: float = Field(
        default=6.0,
        validation_alias=AliasChoices(
            "DITAKNET_UPDATE_CHECK_INTERVAL_HOURS",
            "APP_UPDATE_CHECK_INTERVAL_HOURS",
        ),
    )
    app_update_manifest_url: str = Field(
        default="",
        validation_alias=AliasChoices(
            "DITAKNET_UPDATE_MANIFEST_URL",
            "APP_UPDATE_MANIFEST_URL",
            "APP_UPDATE_CHECK_URL",
        ),
    )
    app_update_stable_manifest_url: str = Field(
        default=(
            "https://raw.githubusercontent.com/ditaknet-sudo/ditaknet/"
            "update-feed/stable.json"
        ),
        validation_alias="DITAKNET_UPDATE_STABLE_MANIFEST_URL",
    )
    app_update_beta_manifest_url: str = Field(
        default=(
            "https://raw.githubusercontent.com/ditaknet-sudo/ditaknet/"
            "update-feed/beta.json"
        ),
        validation_alias="DITAKNET_UPDATE_BETA_MANIFEST_URL",
    )
    app_update_check_url: str = Field(
        default="",
        validation_alias="APP_UPDATE_CHECK_URL",
    )
    app_update_check_timeout_seconds: float = Field(
        default=8.0,
        validation_alias=AliasChoices(
            "DITAKNET_UPDATE_CHECK_TIMEOUT_SECONDS",
            "APP_UPDATE_CHECK_TIMEOUT_SECONDS",
        ),
    )
    app_update_release_url: str = Field(
        default="https://github.com/ditaknet-sudo/ditaknet/releases",
        validation_alias=AliasChoices(
            "DITAKNET_UPDATE_RELEASE_URL",
            "APP_UPDATE_RELEASE_URL",
        ),
    )
    app_update_manifest_signing_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "DITAKNET_UPDATE_MANIFEST_SIGNING_KEY",
            "APP_UPDATE_MANIFEST_SIGNING_KEY",
        ),
    )
    app_update_signature_required: bool = Field(
        default=True,
        validation_alias="DITAKNET_UPDATE_SIGNATURE_REQUIRED",
    )
    app_update_public_keyring_path: str = Field(
        default="",
        validation_alias="DITAKNET_UPDATE_PUBLIC_KEYRING_PATH",
    )
    app_latest_version: str = Field(default="", validation_alias="APP_LATEST_VERSION")
    app_latest_image_tag: str = Field(
        default="", validation_alias="APP_LATEST_IMAGE_TAG"
    )

    # ── Database ──────────────────────────────────────────
    database_url: str = Field(default="", validation_alias="DATABASE_URL")
    database_path: str = Field(
        default="data/ditaknet.db", validation_alias="DATABASE_PATH"
    )

    # ── Directories ───────────────────────────────────────
    data_dir: str = Field(default="data", validation_alias="DATA_DIR")
    backup_dir: str = Field(default="backups", validation_alias="BACKUP_DIR")
    log_dir: str = Field(default="logs", validation_alias="LOG_DIR")

    # ── Logging ───────────────────────────────────────────
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    # ── Check Defaults ────────────────────────────────────
    default_check_interval: int = 60
    default_check_timeout: int = 10
    min_check_interval: int = 10
    check_workers: int = Field(default=5, validation_alias="CHECK_WORKERS")

    # ── State Engine ──────────────────────────────────────
    warning_threshold: int = 1
    critical_threshold: int = 3

    # ── Alert Engine ──────────────────────────────────────
    alert_cooldown_seconds: int = 300

    # ── Telegram ──────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── Plugin System ─────────────────────────────────────
    plugin_dir: str = Field(default="plugins", validation_alias="PLUGIN_DIR")

    # ── Web Dashboard / Auth ──────────────────────────────
    admin_username: str = Field(default="admin", validation_alias="ADMIN_USERNAME")
    admin_password: str = Field(default="change-me", validation_alias="ADMIN_PASSWORD")
    secret_key: str = Field(default="", validation_alias="SECRET_KEY")
    session_secret: str = Field(default="change-me", validation_alias="SESSION_SECRET")
    jwt_secret: str = ""
    jwt_expire_minutes: int = 60
    admin_role: str = "admin"
    cors_allowed_origins: str = Field(
        default=("http://localhost:5833,http://127.0.0.1:5833"),
        validation_alias=AliasChoices("CORS_ALLOWED_ORIGINS", "CORS_ORIGINS"),
    )
    trusted_proxies: str = Field(default="", validation_alias="TRUSTED_PROXIES")
    maintenance_mode: bool = False
    session_cookie_secure: bool = Field(
        default=False,
        validation_alias="SESSION_COOKIE_SECURE",
    )

    # ── Scheduler ─────────────────────────────────────────
    scheduler_enabled: bool = Field(default=True, validation_alias="SCHEDULER_ENABLED")

    # ── Data Retention ────────────────────────────────────
    retention_days: int = Field(default=30, validation_alias="RETENTION_DAYS")
    result_retention_days: int = Field(
        default=0, validation_alias="RESULT_RETENTION_DAYS"
    )
    alert_retention_days: int = Field(
        default=0, validation_alias="ALERT_RETENTION_DAYS"
    )
    metric_retention_days: int = Field(
        default=0, validation_alias="METRIC_RETENTION_DAYS"
    )

    # ── Automatic backups ───────────────────────────────
    auto_backup_enabled: bool = Field(
        default=True, validation_alias="AUTO_BACKUP_ENABLED"
    )
    auto_backup_keep_count: int = Field(
        default=3, validation_alias="AUTO_BACKUP_KEEP_COUNT"
    )

    # ── DitakNet Agent / Metrics ────────────────────────
    agent_registration_key: str = Field(
        default="", validation_alias="AGENT_REGISTRATION_KEY"
    )
    agent_token_header: str = "X-Agent-Token"
    agent_heartbeat_timeout_seconds: int = 120
    agent_heartbeat_check_interval_seconds: int = 30
    metric_cpu_warning: float = 80.0
    metric_cpu_critical: float = 95.0
    metric_memory_warning: float = 80.0
    metric_memory_critical: float = 95.0
    metric_disk_warning: float = 85.0
    metric_disk_critical: float = 95.0

    # ── Network discovery ─────────────────────────────────
    discovery_max_concurrent: int = Field(
        default=8, validation_alias="DISCOVERY_MAX_CONCURRENT"
    )
    discovery_timeout_seconds: float = Field(
        default=2.0,
        validation_alias="DISCOVERY_TIMEOUT_SECONDS",
    )
    discovery_batch_pause_ms: int = Field(
        default=50, validation_alias="DISCOVERY_BATCH_PAUSE_MS"
    )
    discovery_enabled: bool = Field(default=True, validation_alias="DISCOVERY_ENABLED")
    discovery_auto_refresh_enabled: bool = Field(
        default=True, validation_alias="DISCOVERY_AUTO_REFRESH_ENABLED"
    )
    discovery_refresh_interval_minutes: int = Field(
        default=10, validation_alias="DISCOVERY_REFRESH_INTERVAL_MINUTES"
    )
    discovery_stale_after_minutes: int = Field(
        default=30, validation_alias="DISCOVERY_STALE_AFTER_MINUTES"
    )
    discovery_offline_after_minutes: int = Field(
        default=60, validation_alias="DISCOVERY_OFFLINE_AFTER_MINUTES"
    )
    discovery_refresh_scan_mode: str = Field(
        default="quick", validation_alias="DISCOVERY_REFRESH_SCAN_MODE"
    )
    discovery_dns_servers: str = Field(
        default="",
        validation_alias="DISCOVERY_DNS_SERVERS",
        description="Comma-separated LAN DNS servers (router IP) for PTR lookups in Docker.",
    )

    @model_validator(mode="after")
    def _normalize_settings(self) -> "Settings":
        url_path = _sqlite_path_from_url(self.database_url)
        if url_path:
            object.__setattr__(self, "database_path", url_path)

        if self.result_retention_days <= 0 and self.retention_days > 0:
            object.__setattr__(self, "result_retention_days", self.retention_days)

        if self.alert_retention_days <= 0:
            object.__setattr__(self, "alert_retention_days", 180)

        if self.metric_retention_days <= 0:
            object.__setattr__(self, "metric_retention_days", 30)

        update_channel = self.app_update_channel.strip().lower()
        if update_channel not in {"stable", "beta"}:
            raise ValueError("DITAKNET_UPDATE_CHANNEL must be 'stable' or 'beta'")
        object.__setattr__(self, "app_update_channel", update_channel)

        return self

    @property
    def effective_secret_key(self) -> str:
        if self.secret_key.strip():
            return self.secret_key.strip()
        if self.session_secret.strip() not in {"", "change-me"}:
            return self.session_secret.strip()
        return _load_or_create_runtime_secret(self.data_dir)

    @property
    def db_path(self) -> Path:
        return Path(self.database_path).expanduser().resolve()

    @property
    def data_dir_path(self) -> Path:
        return Path(self.data_dir).expanduser().resolve()

    @property
    def backup_dir_path(self) -> Path:
        return Path(self.backup_dir).expanduser().resolve()

    @property
    def log_dir_path(self) -> Path:
        return Path(self.log_dir).expanduser().resolve()

    @property
    def plugin_dir_path(self) -> Path:
        return Path(self.plugin_dir).expanduser().resolve()

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def cors_origins(self) -> list[str]:
        raw = self.cors_allowed_origins or os.getenv("CORS_ALLOWED_ORIGINS", "")
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env.lower() in {"development", "dev", "local"}

    @property
    def database_type(self) -> str:
        if self.database_url.startswith("sqlite:") or not self.database_url:
            return "sqlite"
        return self.database_url.split(":", 1)[0].lower()

    @property
    def release_build_date(self) -> str:
        return self.build_date.strip() or self.app_build_date.strip()

    def safe_system_info(self) -> dict[str, Any]:
        """Return non-secret system configuration for API responses."""
        return {
            "app_name": self.app_name,
            "app_env": self.app_env,
            "app_version": self.app_version,
            "build_commit": self.build_commit.strip() or None,
            "build_date": self.release_build_date or None,
            "image_tag": self.image_tag.strip() or None,
            "github_repository": self.github_repository.strip() or None,
            "ghcr_image": self.ghcr_image.strip() or None,
            "database_type": self.database_type,
            "database_path": str(self.db_path),
            "data_dir": str(self.data_dir_path),
            "backup_dir": str(self.backup_dir_path),
            "log_dir": str(self.log_dir_path),
            "scheduler_enabled": self.scheduler_enabled,
            "telegram_enabled": self.telegram_enabled,
            "retention": {
                "results_days": self.result_retention_days,
                "alerts_days": self.alert_retention_days,
                "metrics_days": self.metric_retention_days,
            },
        }


settings = Settings()
