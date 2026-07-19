"""
SQLite database layer (async aiosqlite, no ORM).

Session lifecycle:
  - ``init_db()`` opens one module-level connection (WAL mode) at startup
  - ``get_db()`` returns that connection; callers must not close it per request
  - ``close_db()`` runs on app shutdown

SQLite file path resolves from ``settings.db_path`` (typically under ``DATA_DIR``).
Schema changes use additive ``MIGRATION_SQL`` strings, not Alembic yet.

Relationships (FK, cascade):
  hosts 1—N services 1—N check_results / alerts
  agents 1—N agent_metrics / agent_alerts (standalone from service checks)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import aiosqlite
from loguru import logger

from ditaknet.config import settings
from ditaknet.core.rbac import ALL_PERMISSIONS, DEFAULT_ROLES, normalize_role, permissions_for_role

# Single shared connection — sufficient for SQLite WAL + async FastAPI workload.
_db: Optional[aiosqlite.Connection] = None
_db_path: Optional[Path] = None
_lock = asyncio.Lock()

# ─── Schema ───────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS hosts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    address     TEXT    NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    manual_classification_enabled INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS services (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    host_id              INTEGER NOT NULL REFERENCES hosts(id) ON DELETE CASCADE,
    name                 TEXT    NOT NULL,
    check_type           TEXT    NOT NULL DEFAULT 'http',
    target               TEXT    NOT NULL,
    port                 INTEGER,
    interval_seconds     INTEGER NOT NULL DEFAULT 60,
    timeout_seconds      INTEGER NOT NULL DEFAULT 10,
    expected_status_code INTEGER DEFAULT 200,
    enabled              INTEGER NOT NULL DEFAULT 1,
    current_state        TEXT    NOT NULL DEFAULT 'unknown',
    created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT
);

CREATE TABLE IF NOT EXISTS check_results (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id       INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    status           TEXT    NOT NULL,
    response_time_ms REAL,
    message          TEXT    DEFAULT '',
    checked_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id   INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    alert_type   TEXT    NOT NULL,
    message      TEXT    NOT NULL,
    severity     TEXT    NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at  TEXT
);

CREATE TABLE IF NOT EXISTS state_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    old_state  TEXT    NOT NULL,
    new_state  TEXT    NOT NULL,
    reason     TEXT    DEFAULT '',
    changed_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor       TEXT    NOT NULL DEFAULT 'system',
    action      TEXT    NOT NULL,
    resource    TEXT    NOT NULL DEFAULT '',
    resource_id TEXT    DEFAULT '',
    detail      TEXT    DEFAULT '',
    ip_address  TEXT    DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS system_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    level         TEXT    NOT NULL DEFAULT 'info',
    category      TEXT    NOT NULL DEFAULT 'application',
    event_type    TEXT    NOT NULL DEFAULT '',
    message       TEXT    NOT NULL DEFAULT '',
    source        TEXT    NOT NULL DEFAULT '',
    entity_type   TEXT    NOT NULL DEFAULT '',
    entity_id     TEXT    NOT NULL DEFAULT '',
    user_id       TEXT    NOT NULL DEFAULT '',
    ip_address    TEXT    NOT NULL DEFAULT '',
    metadata_json TEXT    NOT NULL DEFAULT '{}',
    is_sensitive  INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    id         TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS roles (
    code             TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    permissions_json TEXT NOT NULL DEFAULT '[]',
    is_system        INTEGER NOT NULL DEFAULT 1,
    license_feature  TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    username              TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name             TEXT NOT NULL DEFAULT '',
    email                 TEXT NOT NULL DEFAULT '',
    phone                 TEXT NOT NULL DEFAULT '',
    telegram              TEXT NOT NULL DEFAULT '',
    role                  TEXT NOT NULL DEFAULT 'viewer',
    is_active             INTEGER NOT NULL DEFAULT 1,
    is_superadmin         INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT,
    last_login_at         TEXT,
    password_hash         TEXT NOT NULL,
    must_change_password  INTEGER NOT NULL DEFAULT 0,
    failed_login_count    INTEGER NOT NULL DEFAULT 0,
    locked_until          TEXT,
    permissions_json      TEXT NOT NULL DEFAULT '[]',
    session_version       INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_check_results_service   ON check_results(service_id, checked_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_service           ON alerts(service_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_state_log_service        ON state_log(service_id, changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_services_host            ON services(host_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created       ON audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_system_logs_created      ON system_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_system_logs_level        ON system_logs(level, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_system_logs_category     ON system_logs(category, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_users_role               ON users(role, is_active);
CREATE INDEX IF NOT EXISTS idx_users_last_login         ON users(last_login_at DESC);

CREATE TABLE IF NOT EXISTS agents (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT    NOT NULL,
    hostname           TEXT    NOT NULL DEFAULT '',
    host_id            INTEGER REFERENCES hosts(id) ON DELETE SET NULL,
    token_hash         TEXT    NOT NULL UNIQUE,
    status             TEXT    NOT NULL DEFAULT 'pending',
    last_heartbeat_at  TEXT,
    last_metrics_at    TEXT,
    enabled            INTEGER NOT NULL DEFAULT 1,
    registered_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT
);

CREATE TABLE IF NOT EXISTS agent_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    cpu_percent     REAL    NOT NULL,
    memory_percent  REAL    NOT NULL,
    disk_percent    REAL    NOT NULL,
    collected_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agent_alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id     INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    alert_type   TEXT    NOT NULL,
    message      TEXT    NOT NULL,
    severity     TEXT    NOT NULL,
    metric_name  TEXT    DEFAULT '',
    metric_value REAL,
    threshold    REAL,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_agents_status            ON agents(status, last_heartbeat_at);
CREATE INDEX IF NOT EXISTS idx_agent_metrics_agent      ON agent_metrics(agent_id, collected_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_alerts_agent       ON agent_alerts(agent_id, created_at DESC);

CREATE TABLE IF NOT EXISTS licenses (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    license_key_hash TEXT    NOT NULL DEFAULT '',
    tier             TEXT    NOT NULL DEFAULT 'FREE',
    owner_name       TEXT    NOT NULL DEFAULT '',
    expires_at       TEXT,
    activated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    last_validated_at TEXT,
    status           TEXT    NOT NULL DEFAULT 'active',
    limits_json      TEXT    NOT NULL DEFAULT '{}',
    signature_valid  INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS discovery_scans (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    status           TEXT    NOT NULL DEFAULT 'pending',
    profile          TEXT    NOT NULL DEFAULT 'normal',
    subnets_json     TEXT    NOT NULL DEFAULT '[]',
    progress_percent INTEGER NOT NULL DEFAULT 0,
    total_hosts      INTEGER NOT NULL DEFAULT 0,
    scanned_hosts    INTEGER NOT NULL DEFAULT 0,
    found_count      INTEGER NOT NULL DEFAULT 0,
    failed_probe_count INTEGER NOT NULL DEFAULT 0,
    current_ip       TEXT    NOT NULL DEFAULT '',
    current_subnet   TEXT    NOT NULL DEFAULT '',
    current_stage    TEXT    NOT NULL DEFAULT '',
    stage_message    TEXT    NOT NULL DEFAULT '',
    elapsed_seconds  INTEGER NOT NULL DEFAULT 0,
    probe_methods_json TEXT  NOT NULL DEFAULT '[]',
    diagnostics_json TEXT    NOT NULL DEFAULT '[]',
    diagnostic_meta_json TEXT NOT NULL DEFAULT '{}',
    permission_errors_json TEXT NOT NULL DEFAULT '[]',
    request_id       TEXT    NOT NULL DEFAULT '',
    error_message    TEXT,
    started_at       TEXT,
    finished_at      TEXT,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS discovered_devices (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id             INTEGER NOT NULL REFERENCES discovery_scans(id) ON DELETE CASCADE,
    ip_address          TEXT    NOT NULL,
    mac_address         TEXT    NOT NULL DEFAULT '',
    hostname            TEXT    NOT NULL DEFAULT '',
    vendor              TEXT    NOT NULL DEFAULT '',
    open_ports          TEXT    NOT NULL DEFAULT '[]',
    detected_services   TEXT    NOT NULL DEFAULT '[]',
    detected_type       TEXT    NOT NULL DEFAULT 'unknown',
    confidence          INTEGER NOT NULL DEFAULT 0,
    first_seen_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    last_seen_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    discovery_source    TEXT    NOT NULL DEFAULT '',
    raw_metadata_json   TEXT    NOT NULL DEFAULT '{}',
    imported_host_id    INTEGER REFERENCES hosts(id) ON DELETE SET NULL,
    UNIQUE(scan_id, ip_address)
);

CREATE INDEX IF NOT EXISTS idx_discovery_scans_status ON discovery_scans(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_discovered_devices_scan ON discovered_devices(scan_id);
CREATE INDEX IF NOT EXISTS idx_discovered_devices_ip ON discovered_devices(ip_address);

CREATE TABLE IF NOT EXISTS maintenance_tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT    NOT NULL,
    device_id       INTEGER REFERENCES hosts(id) ON DELETE SET NULL,
    alert_id        INTEGER REFERENCES alerts(id) ON DELETE SET NULL,
    priority        TEXT    NOT NULL DEFAULT 'medium',
    status          TEXT    NOT NULL DEFAULT 'open',
    recommendation  TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_maintenance_tasks_status ON maintenance_tasks(status, created_at DESC);

CREATE TABLE IF NOT EXISTS employees (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name                TEXT    NOT NULL,
    department               TEXT    NOT NULL DEFAULT '',
    position                 TEXT    NOT NULL DEFAULT '',
    email                    TEXT    DEFAULT '',
    phone                    TEXT    DEFAULT '',
    employee_code            TEXT    DEFAULT '',
    status                   TEXT    NOT NULL DEFAULT 'active',
    privacy_notice_accepted  INTEGER NOT NULL DEFAULT 0,
    created_at               TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at               TEXT
);

CREATE TABLE IF NOT EXISTS employee_devices (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id    INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    device_name    TEXT    NOT NULL,
    device_type    TEXT    NOT NULL DEFAULT 'laptop',
    mac_address    TEXT    DEFAULT '',
    hostname       TEXT    DEFAULT '',
    static_ip      TEXT    DEFAULT '',
    last_ip        TEXT    DEFAULT '',
    agent_id       TEXT    DEFAULT '',
    is_primary     INTEGER NOT NULL DEFAULT 0,
    is_approved    INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT
);

CREATE TABLE IF NOT EXISTS employee_presence (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id           INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    device_id             INTEGER REFERENCES employee_devices(id) ON DELETE SET NULL,
    status                TEXT    NOT NULL DEFAULT 'unknown',
    connection_type       TEXT    NOT NULL DEFAULT 'unknown',
    confidence            TEXT    NOT NULL DEFAULT 'low',
    current_ip            TEXT    DEFAULT '',
    detected_mac          TEXT    DEFAULT '',
    detected_hostname     TEXT    DEFAULT '',
    source                TEXT    NOT NULL DEFAULT 'manual_update',
    first_seen_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    last_seen_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    last_status_change_at TEXT    NOT NULL DEFAULT (datetime('now')),
    notes                 TEXT    DEFAULT '',
    UNIQUE(employee_id)
);

CREATE TABLE IF NOT EXISTS employee_presence_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id  INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    device_id    INTEGER REFERENCES employee_devices(id) ON DELETE SET NULL,
    old_status   TEXT    DEFAULT '',
    new_status   TEXT    NOT NULL,
    source       TEXT    NOT NULL DEFAULT 'manual_update',
    ip           TEXT    DEFAULT '',
    confidence   TEXT    NOT NULL DEFAULT 'low',
    event_time   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS privacy_audit_logs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id      TEXT    NOT NULL DEFAULT 'system',
    action             TEXT    NOT NULL,
    target_employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    details            TEXT    DEFAULT '',
    created_at         TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_employees_status ON employees(status, department);
CREATE INDEX IF NOT EXISTS idx_employee_devices_employee ON employee_devices(employee_id);
CREATE INDEX IF NOT EXISTS idx_employee_devices_mac ON employee_devices(mac_address);
CREATE INDEX IF NOT EXISTS idx_employee_presence_status ON employee_presence(status, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_employee_presence_events_employee ON employee_presence_events(employee_id, event_time DESC);
CREATE INDEX IF NOT EXISTS idx_privacy_audit_logs_created ON privacy_audit_logs(created_at DESC);

CREATE TABLE IF NOT EXISTS departments (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT    NOT NULL,
    description      TEXT    DEFAULT '',
    manager_user_id  TEXT    DEFAULT '',
    is_active        INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS employee_groups (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    description       TEXT    DEFAULT '',
    department_id     INTEGER REFERENCES departments(id) ON DELETE SET NULL,
    default_shift_id  INTEGER,
    is_active         INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT
);

CREATE TABLE IF NOT EXISTS shifts (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    name                      TEXT    NOT NULL,
    start_time                TEXT    NOT NULL,
    end_time                  TEXT    NOT NULL,
    timezone                  TEXT    NOT NULL DEFAULT 'UTC',
    break_minutes             INTEGER NOT NULL DEFAULT 0,
    grace_late_minutes        INTEGER NOT NULL DEFAULT 10,
    grace_leave_early_minutes INTEGER NOT NULL DEFAULT 10,
    expected_work_minutes     INTEGER NOT NULL DEFAULT 480,
    color                     TEXT    DEFAULT '',
    is_overnight              INTEGER NOT NULL DEFAULT 0,
    is_active                 INTEGER NOT NULL DEFAULT 1,
    created_at                TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at                TEXT
);

CREATE TABLE IF NOT EXISTS shift_assignments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id   INTEGER REFERENCES employees(id) ON DELETE CASCADE,
    department_id INTEGER REFERENCES departments(id) ON DELETE CASCADE,
    group_id      INTEGER REFERENCES employee_groups(id) ON DELETE CASCADE,
    shift_id      INTEGER NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
    valid_from    TEXT    NOT NULL,
    valid_to      TEXT,
    weekday_rules TEXT    DEFAULT '',
    priority      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS attendance_days (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id           INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    date                  TEXT    NOT NULL,
    shift_id              INTEGER REFERENCES shifts(id) ON DELETE SET NULL,
    expected_start        TEXT,
    expected_end          TEXT,
    expected_work_minutes INTEGER NOT NULL DEFAULT 0,
    first_seen_at         TEXT,
    last_seen_at          TEXT,
    worked_minutes        INTEGER NOT NULL DEFAULT 0,
    break_minutes         INTEGER NOT NULL DEFAULT 0,
    late_minutes          INTEGER NOT NULL DEFAULT 0,
    early_leave_minutes   INTEGER NOT NULL DEFAULT 0,
    overtime_minutes      INTEGER NOT NULL DEFAULT 0,
    absence_minutes       INTEGER NOT NULL DEFAULT 0,
    status                TEXT    NOT NULL DEFAULT 'unknown',
    confidence            TEXT    NOT NULL DEFAULT 'low',
    source_summary        TEXT    DEFAULT '',
    manually_adjusted     INTEGER NOT NULL DEFAULT 0,
    manual_note           TEXT    DEFAULT '',
    created_at            TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT,
    UNIQUE(employee_id, date)
);

CREATE TABLE IF NOT EXISTS attendance_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id  INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    device_id    INTEGER REFERENCES employee_devices(id) ON DELETE SET NULL,
    event_type   TEXT    NOT NULL,
    event_time   TEXT    NOT NULL,
    source       TEXT    NOT NULL DEFAULT 'manual',
    ip           TEXT    DEFAULT '',
    mac          TEXT    DEFAULT '',
    hostname     TEXT    DEFAULT '',
    confidence   TEXT    NOT NULL DEFAULT 'low',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_departments_active ON departments(is_active, name);
CREATE INDEX IF NOT EXISTS idx_employee_groups_dept ON employee_groups(department_id);
CREATE INDEX IF NOT EXISTS idx_shifts_active ON shifts(is_active);
CREATE INDEX IF NOT EXISTS idx_shift_assignments_lookup ON shift_assignments(employee_id, department_id, group_id, valid_from);
CREATE INDEX IF NOT EXISTS idx_attendance_days_date ON attendance_days(date, employee_id);
CREATE INDEX IF NOT EXISTS idx_attendance_events_employee ON attendance_events(employee_id, event_time DESC);

CREATE TABLE IF NOT EXISTS offices (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    name               TEXT    NOT NULL,
    code               TEXT    NOT NULL UNIQUE,
    address            TEXT    DEFAULT '',
    city               TEXT    DEFAULT '',
    timezone           TEXT    NOT NULL DEFAULT 'UTC',
    subnet_cidr        TEXT    DEFAULT '',
    public_ip          TEXT    DEFAULT '',
    status             TEXT    NOT NULL DEFAULT 'active',
    branch_token_hash  TEXT    NOT NULL DEFAULT '',
    last_agent_seen_at TEXT,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT
);

CREATE TABLE IF NOT EXISTS branch_agents (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    office_id        INTEGER NOT NULL REFERENCES offices(id) ON DELETE CASCADE,
    hostname         TEXT    NOT NULL DEFAULT '',
    agent_version    TEXT    NOT NULL DEFAULT '',
    local_subnet     TEXT    NOT NULL DEFAULT '',
    scan_status      TEXT    NOT NULL DEFAULT 'unknown',
    last_heartbeat_at TEXT,
    is_active        INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT,
    UNIQUE(office_id, hostname)
);

CREATE TABLE IF NOT EXISTS branch_presence_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    office_id    INTEGER NOT NULL REFERENCES offices(id) ON DELETE CASCADE,
    branch_agent_id INTEGER REFERENCES branch_agents(id) ON DELETE SET NULL,
    employee_id  INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    device_id    INTEGER REFERENCES employee_devices(id) ON DELETE SET NULL,
    detected_at  TEXT    NOT NULL,
    mac_address  TEXT    DEFAULT '',
    hostname     TEXT    DEFAULT '',
    ip_address   TEXT    DEFAULT '',
    source       TEXT    NOT NULL DEFAULT 'branch_agent',
    confidence   TEXT    NOT NULL DEFAULT 'low',
    payload_json TEXT    DEFAULT '{}',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notifications (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    level         TEXT    NOT NULL DEFAULT 'info',
    category      TEXT    NOT NULL DEFAULT 'system',
    title         TEXT    NOT NULL,
    message       TEXT    NOT NULL,
    action_url    TEXT    DEFAULT '',
    read_at       TEXT,
    dismissed_at  TEXT,
    metadata_json TEXT    DEFAULT '{}',
    dedupe_key    TEXT    DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_offices_code ON offices(code);
CREATE INDEX IF NOT EXISTS idx_offices_status ON offices(status);
CREATE INDEX IF NOT EXISTS idx_branch_agents_office ON branch_agents(office_id, last_heartbeat_at DESC);
CREATE INDEX IF NOT EXISTS idx_branch_presence_office ON branch_presence_events(office_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_dedupe ON notifications(dedupe_key);

"""

# Lightweight migrations for existing databases (ignored if column exists)
MIGRATION_SQL = [
    "ALTER TABLE hosts ADD COLUMN host_type TEXT NOT NULL DEFAULT 'server'",
    "ALTER TABLE hosts ADD COLUMN location TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE hosts ADD COLUMN tags TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE services ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE services ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3",
    "ALTER TABLE alerts ADD COLUMN notification_sent INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE hosts ADD COLUMN parent_device_id INTEGER REFERENCES hosts(id) ON DELETE SET NULL",
    "ALTER TABLE hosts ADD COLUMN network_segment TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE hosts ADD COLUMN rack_or_room TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE hosts ADD COLUMN manual_classification_enabled INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE employees ADD COLUMN department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL",
    "ALTER TABLE employees ADD COLUMN group_id INTEGER REFERENCES employee_groups(id) ON DELETE SET NULL",
    "ALTER TABLE employees ADD COLUMN default_shift_id INTEGER REFERENCES shifts(id) ON DELETE SET NULL",
    "ALTER TABLE employees ADD COLUMN employment_status TEXT NOT NULL DEFAULT 'active'",
    "ALTER TABLE employees ADD COLUMN hire_date TEXT",
    "ALTER TABLE employees ADD COLUMN notes TEXT DEFAULT ''",
    "ALTER TABLE employees ADD COLUMN default_office_id INTEGER REFERENCES offices(id) ON DELETE SET NULL",
    "ALTER TABLE employees ADD COLUMN allow_multi_office_presence INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE employee_presence ADD COLUMN office_id INTEGER REFERENCES offices(id) ON DELETE SET NULL",
    "ALTER TABLE employee_presence ADD COLUMN branch_agent_id INTEGER REFERENCES branch_agents(id) ON DELETE SET NULL",
    "ALTER TABLE attendance_days ADD COLUMN office_id INTEGER REFERENCES offices(id) ON DELETE SET NULL",
    "ALTER TABLE attendance_days ADD COLUMN worked_office_summary TEXT DEFAULT ''",
    "ALTER TABLE attendance_events ADD COLUMN office_id INTEGER REFERENCES offices(id) ON DELETE SET NULL",
    "ALTER TABLE users ADD COLUMN full_name TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE users ADD COLUMN phone TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE users ADD COLUMN telegram TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'viewer'",
    "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE users ADD COLUMN is_superadmin INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN updated_at TEXT",
    "ALTER TABLE users ADD COLUMN last_login_at TEXT",
    "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN failed_login_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN locked_until TEXT",
    "ALTER TABLE users ADD COLUMN permissions_json TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE users ADD COLUMN session_version INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE roles ADD COLUMN description TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE roles ADD COLUMN permissions_json TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE roles ADD COLUMN is_system INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE roles ADD COLUMN license_feature TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE roles ADD COLUMN updated_at TEXT",
    "ALTER TABLE discovery_scans ADD COLUMN failed_probe_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE discovery_scans ADD COLUMN current_ip TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE discovery_scans ADD COLUMN current_subnet TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE discovery_scans ADD COLUMN current_stage TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE discovery_scans ADD COLUMN stage_message TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE discovery_scans ADD COLUMN elapsed_seconds INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE discovery_scans ADD COLUMN probe_methods_json TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE discovery_scans ADD COLUMN diagnostics_json TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE discovery_scans ADD COLUMN diagnostic_meta_json TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE discovery_scans ADD COLUMN permission_errors_json TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE discovery_scans ADD COLUMN request_id TEXT NOT NULL DEFAULT ''",
]


# ─── Connection Management ────────────────────────────────


async def init_db(db_path: Optional[str] = None) -> aiosqlite.Connection:
    """Initialise the database: create file, enable WAL, run schema."""
    global _db, _db_path

    path = Path(db_path) if db_path else settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    _db_path = path.resolve()

    logger.info("Opening database at {}", path)
    _db = await aiosqlite.connect(str(path))
    _db.row_factory = aiosqlite.Row

    # Performance pragmas
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA synchronous=NORMAL")
    await _db.execute("PRAGMA foreign_keys=ON")

    # Create tables
    await _db.executescript(SCHEMA_SQL)
    await _run_migrations(_db)
    from ditaknet.discovery.store import ensure_discovery_schema

    await ensure_discovery_schema(_db)
    await ensure_user_rbac_defaults(_db)
    await _db.commit()

    logger.info("Database initialised successfully")
    return _db


async def _run_migrations(connection: aiosqlite.Connection) -> None:
    """Apply additive schema migrations for existing databases."""
    for sql in MIGRATION_SQL:
        # The ID follows the migration content, not its list position. Future
        # insertions/reordering therefore cannot cause an unrelated migration
        # to be skipped by an old positional ledger entry.
        migration_id = _migration_id(sql)
        applied = await connection.execute_fetchall(
            "SELECT 1 FROM schema_migrations WHERE id = ?",
            (migration_id,),
        )
        if applied:
            continue
        try:
            await connection.execute(sql)
        except aiosqlite.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
        # Fresh databases already contain the current columns via SCHEMA_SQL.
        # Record those migrations as applied too, so every later startup can
        # skip the duplicate ALTER statements deterministically.
        await connection.execute(
            "INSERT OR IGNORE INTO schema_migrations (id, applied_at) VALUES (?, ?)",
            (migration_id, _now()),
        )


def _migration_id(sql: str) -> str:
    digest = hashlib.sha256(sql.strip().encode("utf-8")).hexdigest()[:16]
    return f"additive-{digest}"


async def get_db() -> aiosqlite.Connection:
    """Return the active database connection."""
    if _db is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    return _db


CORE_TABLES = (
    "hosts",
    "services",
    "check_results",
    "alerts",
    "state_log",
    "audit_logs",
    "system_logs",
    "app_settings",
    "schema_migrations",
    "users",
    "roles",
    "licenses",
    "discovery_scans",
    "discovered_devices",
    "monitored_networks",
)


async def schema_health() -> dict:
    """Report whether expected SQLite tables exist (migration sanity)."""
    try:
        connection = await get_db()
        rows = await connection.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        existing = {str(row[0]) for row in rows}
        missing = [name for name in CORE_TABLES if name not in existing]
        return {
            "ok": not missing,
            "status": "pass" if not missing else "fail",
            "tables_expected": len(CORE_TABLES),
            "tables_present": len(existing),
            "missing_tables": missing,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "fail",
            "error": type(exc).__name__,
            "missing_tables": list(CORE_TABLES),
        }


async def close_db() -> None:
    """Close the database connection."""
    global _db, _db_path
    if _db is not None:
        await _db.close()
        _db = None
        _db_path = None
        logger.info("Database connection closed")


_FACTORY_RESET_TABLES = (
    "check_results",
    "alerts",
    "state_log",
    "agent_alerts",
    "agent_metrics",
    "services",
    "discovered_devices",
    "discovery_scans",
    "agents",
    "maintenance_tasks",
    "attendance_events",
    "attendance_days",
    "branch_presence_events",
    "branch_agents",
    "offices",
    "shift_assignments",
    "shifts",
    "employee_groups",
    "departments",
    "employee_presence_events",
    "employee_presence",
    "employee_devices",
    "privacy_audit_logs",
    "employees",
    "hosts",
    "system_logs",
    "audit_logs",
    "licenses",
    "users",
    "app_settings",
)


async def wipe_for_factory_reset() -> None:
    """Clear all application data in the open database (Windows-safe fallback)."""
    db_conn = await get_db()
    await db_conn.execute("PRAGMA foreign_keys=OFF")
    try:
        for table in _FACTORY_RESET_TABLES:
            await db_conn.execute(f"DELETE FROM {table}")
        await db_conn.execute("DELETE FROM sqlite_sequence")
        await db_conn.commit()
    finally:
        await db_conn.execute("PRAGMA foreign_keys=ON")
        await db_conn.commit()
    logger.info("Factory reset wipe completed in place")


# ─── Helpers ──────────────────────────────────────────────


def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    """Convert an aiosqlite.Row to a plain dict."""
    return dict(row)


def _now() -> str:
    """Current UTC timestamp as ISO string."""
    return datetime.now(UTC).isoformat()


def get_db_path() -> Path:
    """Return the active SQLite database path, or the configured path."""
    return _db_path or settings.db_path.resolve()


# RBAC / User Management


def _clean_permissions(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        try:
            parsed = json.loads(values)
        except Exception:
            parsed = [item.strip() for item in values.split(",")]
    else:
        parsed = values
    result: list[str] = []
    if isinstance(parsed, (list, tuple, set)):
        for item in parsed:
            permission = str(item or "").strip()
            if permission and permission in ALL_PERMISSIONS and permission not in result:
                result.append(permission)
    return sorted(result)


def _permissions_json(values: Any) -> str:
    return json.dumps(_clean_permissions(values), separators=(",", ":"))


def _user_row_to_dict(row: aiosqlite.Row | dict[str, Any]) -> dict[str, Any]:
    data = _row_to_dict(row) if isinstance(row, aiosqlite.Row) else dict(row)
    explicit = _clean_permissions(data.get("permissions_json", "[]"))
    role = normalize_role(data.get("role"))
    data["role"] = role
    data["permissions"] = sorted(set(permissions_for_role(role)) | set(explicit))
    data["explicit_permissions"] = explicit
    data["is_active"] = bool(data.get("is_active"))
    data["is_superadmin"] = bool(data.get("is_superadmin"))
    data["must_change_password"] = bool(data.get("must_change_password"))
    return data


def _role_row_to_dict(row: aiosqlite.Row | dict[str, Any]) -> dict[str, Any]:
    data = _row_to_dict(row) if isinstance(row, aiosqlite.Row) else dict(row)
    data["permissions"] = _clean_permissions(data.get("permissions_json", "[]"))
    data["is_system"] = bool(data.get("is_system"))
    return data


async def ensure_user_rbac_defaults(connection: aiosqlite.Connection | None = None) -> None:
    """Seed system roles and the first super admin without overwriting user data."""
    conn = connection or await get_db()
    now = _now()
    for role in DEFAULT_ROLES.values():
        await conn.execute(
            """INSERT INTO roles
               (code, name, description, permissions_json, is_system, license_feature, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(code) DO UPDATE SET
                   name = excluded.name,
                   description = excluded.description,
                   permissions_json = excluded.permissions_json,
                   is_system = excluded.is_system,
                   license_feature = excluded.license_feature,
                   updated_at = excluded.updated_at""",
            (
                role.code,
                role.name,
                role.description,
                _permissions_json(role.permissions),
                int(role.is_system),
                role.license_feature,
                now,
                now,
            ),
        )

    rows = await conn.execute_fetchall("SELECT COUNT(*) AS cnt FROM users")
    if int(rows[0]["cnt"] if rows else 0) == 0:
        stored_user_rows = await conn.execute_fetchall(
            "SELECT value FROM app_settings WHERE key = 'admin_username'"
        )
        stored_hash_rows = await conn.execute_fetchall(
            "SELECT value FROM app_settings WHERE key = 'admin_password_hash'"
        )
        username = (
            str(stored_user_rows[0]["value"]).strip()
            if stored_user_rows
            else settings.admin_username.strip()
        )
        password_hash = str(stored_hash_rows[0]["value"]) if stored_hash_rows else ""
        if not password_hash:
            bootstrap_password = settings.admin_password.strip()
            if bootstrap_password.lower() not in {
                "",
                "change-me",
                "changeme",
                "admin",
                "password",
            }:
                from ditaknet.security import hash_password

                password_hash = hash_password(bootstrap_password)
        if password_hash:
            await conn.execute(
                """INSERT INTO users
                   (username, full_name, email, role, is_active, is_superadmin, password_hash,
                    must_change_password, failed_login_count, permissions_json, session_version,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, 1, 1, ?, 0, 0, ?, 0, ?, ?)""",
                (
                    username or "admin",
                    "DitakNet Owner",
                    "",
                    "super_admin",
                    password_hash,
                    _permissions_json([]),
                    now,
                    now,
                ),
            )


async def count_users() -> int:
    conn = await get_db()
    rows = await conn.execute_fetchall("SELECT COUNT(*) AS cnt FROM users")
    return int(rows[0]["cnt"] if rows else 0)


async def get_user_by_username(username: str) -> Optional[dict]:
    conn = await get_db()
    rows = await conn.execute_fetchall(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
        (str(username or "").strip(),),
    )
    return _user_row_to_dict(rows[0]) if rows else None


async def get_user_by_id(user_id: int) -> Optional[dict]:
    conn = await get_db()
    rows = await conn.execute_fetchall("SELECT * FROM users WHERE id = ?", (int(user_id),))
    return _user_row_to_dict(rows[0]) if rows else None


async def list_users(*, include_inactive: bool = True) -> list[dict]:
    conn = await get_db()
    query = "SELECT * FROM users"
    params: list[Any] = []
    if not include_inactive:
        query += " WHERE is_active = ?"
        params.append(1)
    query += " ORDER BY is_superadmin DESC, username COLLATE NOCASE"
    rows = await conn.execute_fetchall(query, params)
    return [_user_row_to_dict(row) for row in rows]


async def create_user(
    *,
    username: str,
    password_hash: str,
    full_name: str = "",
    email: str = "",
    phone: str = "",
    telegram: str = "",
    role: str = "viewer",
    is_active: bool = True,
    is_superadmin: bool = False,
    must_change_password: bool = True,
    permissions: Any = None,
) -> dict:
    conn = await get_db()
    now = _now()
    normalized_role = "super_admin" if is_superadmin else normalize_role(role)
    cursor = await conn.execute(
        """INSERT INTO users
           (username, full_name, email, phone, telegram, role, is_active, is_superadmin,
            password_hash, must_change_password, permissions_json, session_version,
            failed_login_count, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)""",
        (
            username.strip(),
            full_name.strip(),
            email.strip(),
            phone.strip(),
            telegram.strip(),
            normalized_role,
            int(is_active),
            int(is_superadmin),
            password_hash,
            int(must_change_password),
            _permissions_json(permissions),
            now,
            now,
        ),
    )
    await conn.commit()
    created = await get_user_by_id(int(cursor.lastrowid))
    if created is None:
        raise RuntimeError("Created user could not be loaded")
    return created


async def update_user(user_id: int, **fields: Any) -> Optional[dict]:
    allowed = {
        "full_name",
        "email",
        "phone",
        "telegram",
        "role",
        "is_active",
        "is_superadmin",
        "must_change_password",
        "locked_until",
        "permissions_json",
    }
    assignments: list[str] = []
    params: list[Any] = []
    for key, value in fields.items():
        if key == "permissions":
            key = "permissions_json"
            value = _permissions_json(value)
        if key == "role":
            value = normalize_role(value)
        if key in {"is_active", "is_superadmin", "must_change_password"}:
            value = int(bool(value))
        if key not in allowed:
            continue
        assignments.append(f"{key} = ?")
        params.append(value)
    if not assignments:
        return await get_user_by_id(user_id)
    assignments.append("updated_at = ?")
    params.append(_now())
    params.append(int(user_id))
    conn = await get_db()
    await conn.execute(f"UPDATE users SET {', '.join(assignments)} WHERE id = ?", params)
    await conn.commit()
    return await get_user_by_id(user_id)


async def update_user_password(
    user_id: int,
    password_hash: str,
    *,
    must_change_password: bool = True,
) -> Optional[dict]:
    conn = await get_db()
    await conn.execute(
        """UPDATE users
           SET password_hash = ?, must_change_password = ?, failed_login_count = 0,
               locked_until = NULL, session_version = session_version + 1, updated_at = ?
           WHERE id = ?""",
        (password_hash, int(must_change_password), _now(), int(user_id)),
    )
    await conn.commit()
    return await get_user_by_id(user_id)


async def set_user_active(user_id: int, active: bool) -> Optional[dict]:
    conn = await get_db()
    await conn.execute(
        """UPDATE users
           SET is_active = ?, session_version = session_version + 1, updated_at = ?
           WHERE id = ?""",
        (int(active), _now(), int(user_id)),
    )
    await conn.commit()
    return await get_user_by_id(user_id)


async def revoke_user_sessions(user_id: int) -> Optional[dict]:
    conn = await get_db()
    await conn.execute(
        "UPDATE users SET session_version = session_version + 1, updated_at = ? WHERE id = ?",
        (_now(), int(user_id)),
    )
    await conn.commit()
    return await get_user_by_id(user_id)


async def record_user_login(user_id: int) -> None:
    conn = await get_db()
    await conn.execute(
        """UPDATE users
           SET last_login_at = ?, failed_login_count = 0, locked_until = NULL, updated_at = ?
           WHERE id = ?""",
        (_now(), _now(), int(user_id)),
    )
    await conn.commit()


async def record_failed_login(
    username: str,
    *,
    threshold: int = 5,
    lock_minutes: int = 15,
) -> Optional[dict]:
    user = await get_user_by_username(username)
    if not user:
        return None
    failed_count = int(user.get("failed_login_count") or 0) + 1
    locked_until = None
    if failed_count >= threshold:
        locked_until = (datetime.now(UTC) + timedelta(minutes=lock_minutes)).isoformat()
    conn = await get_db()
    await conn.execute(
        """UPDATE users
           SET failed_login_count = ?, locked_until = ?, updated_at = ?
           WHERE id = ?""",
        (failed_count, locked_until, _now(), int(user["id"])),
    )
    await conn.commit()
    return await get_user_by_id(int(user["id"]))


async def list_roles() -> list[dict]:
    conn = await get_db()
    rows = await conn.execute_fetchall("SELECT * FROM roles ORDER BY is_system DESC, name")
    return [_role_row_to_dict(row) for row in rows]


async def get_role(code: str) -> Optional[dict]:
    conn = await get_db()
    rows = await conn.execute_fetchall(
        "SELECT * FROM roles WHERE code = ?",
        (normalize_role(code),),
    )
    return _role_row_to_dict(rows[0]) if rows else None


async def upsert_role(
    *,
    code: str,
    name: str,
    description: str = "",
    permissions: Any = None,
    is_system: bool = False,
    license_feature: str = "",
) -> dict:
    conn = await get_db()
    now = _now()
    normalized_code = normalize_role(code) if code in DEFAULT_ROLES else code.strip().lower()
    await conn.execute(
        """INSERT INTO roles
           (code, name, description, permissions_json, is_system, license_feature, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(code) DO UPDATE SET
               name = excluded.name,
               description = excluded.description,
               permissions_json = excluded.permissions_json,
               is_system = excluded.is_system,
               license_feature = excluded.license_feature,
               updated_at = excluded.updated_at""",
        (
            normalized_code,
            name.strip(),
            description.strip(),
            _permissions_json(permissions),
            int(is_system),
            license_feature.strip(),
            now,
            now,
        ),
    )
    await conn.commit()
    role = await get_role(normalized_code)
    if role is None:
        raise RuntimeError("Role could not be loaded")
    return role


async def list_user_activity(username: str, *, limit: int = 25) -> list[dict]:
    return await list_audit_logs(limit=limit, offset=0, actor=username)


# ─── Host CRUD ────────────────────────────────────────────


async def create_host(
    name: str,
    address: str,
    enabled: bool = True,
    host_type: str = "server",
    location: str = "",
    tags: str = "",
    parent_device_id: int | None = None,
    network_segment: str = "",
    rack_or_room: str = "",
) -> dict:
    db = await get_db()
    cursor = await db.execute(
        """INSERT INTO hosts
           (name, address, enabled, host_type, location, tags,
            parent_device_id, network_segment, rack_or_room, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            name,
            address,
            int(enabled),
            host_type,
            location,
            tags,
            parent_device_id,
            network_segment,
            rack_or_room,
            _now(),
        ),
    )
    await db.commit()
    row = await db.execute_fetchall("SELECT * FROM hosts WHERE id = ?", (cursor.lastrowid,))
    return _row_to_dict(row[0])


async def get_host(host_id: int) -> Optional[dict]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM hosts WHERE id = ?", (host_id,))
    return _row_to_dict(rows[0]) if rows else None


async def list_hosts() -> list[dict]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM hosts ORDER BY id")
    return [_row_to_dict(r) for r in rows]


async def update_host(host_id: int, **fields) -> Optional[dict]:
    db = await get_db()
    sets, vals = [], []
    for key in (
        "name",
        "address",
        "enabled",
        "host_type",
        "location",
        "tags",
        "parent_device_id",
        "network_segment",
        "rack_or_room",
        "manual_classification_enabled",
    ):
        if key in fields and fields[key] is not None:
            val = fields[key]
            if key in {"enabled", "manual_classification_enabled"}:
                val = int(val)
            sets.append(f"{key} = ?")
            vals.append(val)
    if not sets:
        return await get_host(host_id)
    sets.append("updated_at = ?")
    vals.append(_now())
    vals.append(host_id)
    await db.execute(f"UPDATE hosts SET {', '.join(sets)} WHERE id = ?", vals)
    await db.commit()
    return await get_host(host_id)


async def delete_host(host_id: int) -> bool:
    db = await get_db()
    cursor = await db.execute("DELETE FROM hosts WHERE id = ?", (host_id,))
    await db.commit()
    return cursor.rowcount > 0


# ─── Service CRUD ─────────────────────────────────────────


async def create_service(
    host_id: int,
    name: str,
    check_type: str,
    target: str,
    port: Optional[int] = None,
    interval_seconds: int = 60,
    timeout_seconds: int = 10,
    expected_status_code: Optional[int] = 200,
    enabled: bool = True,
    retry_count: int = 0,
    max_attempts: int = 3,
) -> dict:
    db = await get_db()
    cursor = await db.execute(
        """INSERT INTO services
           (host_id, name, check_type, target, port, interval_seconds,
            timeout_seconds, expected_status_code, enabled, retry_count,
            max_attempts, current_state, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', ?)""",
        (host_id, name, check_type, target, port, interval_seconds,
         timeout_seconds, expected_status_code, int(enabled),
         retry_count, max_attempts, _now()),
    )
    await db.commit()
    rows = await db.execute_fetchall("SELECT * FROM services WHERE id = ?", (cursor.lastrowid,))
    return _row_to_dict(rows[0])


async def get_service(service_id: int) -> Optional[dict]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM services WHERE id = ?", (service_id,))
    return _row_to_dict(rows[0]) if rows else None


async def list_services(host_id: Optional[int] = None) -> list[dict]:
    db = await get_db()
    if host_id is not None:
        rows = await db.execute_fetchall(
            "SELECT * FROM services WHERE host_id = ? ORDER BY id", (host_id,)
        )
    else:
        rows = await db.execute_fetchall("SELECT * FROM services ORDER BY id")
    return [_row_to_dict(r) for r in rows]


async def update_service(service_id: int, **fields) -> Optional[dict]:
    db = await get_db()
    allowed = (
        "host_id", "name", "check_type", "target", "port", "interval_seconds",
        "timeout_seconds", "expected_status_code", "enabled", "current_state",
        "retry_count", "max_attempts",
    )
    sets, vals = [], []
    for key in allowed:
        if key in fields and fields[key] is not None:
            val = fields[key]
            if key == "enabled":
                val = int(val)
            sets.append(f"{key} = ?")
            vals.append(val)
    if not sets:
        return await get_service(service_id)
    sets.append("updated_at = ?")
    vals.append(_now())
    vals.append(service_id)
    await db.execute(f"UPDATE services SET {', '.join(sets)} WHERE id = ?", vals)
    await db.commit()
    return await get_service(service_id)


async def delete_service(service_id: int) -> bool:
    db = await get_db()
    cursor = await db.execute("DELETE FROM services WHERE id = ?", (service_id,))
    await db.commit()
    return cursor.rowcount > 0


# ─── Check Results ────────────────────────────────────────


async def insert_check_result(
    service_id: int,
    status: str,
    response_time_ms: Optional[float] = None,
    message: str = "",
) -> dict:
    db = await get_db()
    cursor = await db.execute(
        """INSERT INTO check_results (service_id, status, response_time_ms, message, checked_at)
           VALUES (?, ?, ?, ?, ?)""",
        (service_id, status, response_time_ms, message, _now()),
    )
    await db.commit()
    rows = await db.execute_fetchall("SELECT * FROM check_results WHERE id = ?", (cursor.lastrowid,))
    return _row_to_dict(rows[0])


async def get_latest_check(service_id: int) -> Optional[dict]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM check_results WHERE service_id = ? ORDER BY checked_at DESC LIMIT 1",
        (service_id,),
    )
    return _row_to_dict(rows[0]) if rows else None


async def list_check_results(
    service_id: Optional[int] = None,
    host_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    db = await get_db()
    query = "SELECT cr.* FROM check_results cr"
    joins = []
    conditions = []
    params: list[Any] = []

    if host_id is not None:
        joins.append("JOIN services s ON s.id = cr.service_id")
        conditions.append("s.host_id = ?")
        params.append(host_id)

    if service_id is not None:
        conditions.append("cr.service_id = ?")
        params.append(service_id)

    if status is not None:
        conditions.append("cr.status = ?")
        params.append(status)

    if joins:
        query += " " + " ".join(joins)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY cr.checked_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = await db.execute_fetchall(query, params)
    return [_row_to_dict(r) for r in rows]


async def list_host_check_history_since(
    host_id: int,
    *,
    since: str,
    limit: int = 10000,
) -> list[dict]:
    """Check results for all services on a host since *since* (ISO timestamp)."""
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT cr.*, s.name AS service_name, s.check_type, s.target, s.host_id
           FROM check_results cr
           JOIN services s ON s.id = cr.service_id
           WHERE s.host_id = ? AND cr.checked_at >= ?
           ORDER BY cr.checked_at DESC
           LIMIT ?""",
        (host_id, since, limit),
    )
    return [_row_to_dict(r) for r in rows]


async def list_state_changes_for_host(
    host_id: int,
    *,
    since_hours: int | None = None,
    limit: int = 200,
) -> list[dict]:
    db = await get_db()
    params: list[Any] = [host_id]
    query = """SELECT sl.*, s.name AS service_name, s.check_type
               FROM state_log sl
               JOIN services s ON s.id = sl.service_id
               WHERE s.host_id = ?"""
    if since_hours is not None:
        cutoff = (datetime.now(UTC) - timedelta(hours=since_hours)).isoformat()
        query += " AND sl.changed_at >= ?"
        params.append(cutoff)
    query += " ORDER BY sl.changed_at DESC LIMIT ?"
    params.append(limit)
    rows = await db.execute_fetchall(query, params)
    return [_row_to_dict(r) for r in rows]


async def get_latest_checks_all() -> list[dict]:
    """Get the latest check result for every service."""
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT cr.* FROM check_results cr
           INNER JOIN (
               SELECT service_id, MAX(checked_at) AS max_at
               FROM check_results GROUP BY service_id
           ) latest ON cr.service_id = latest.service_id AND cr.checked_at = latest.max_at
           ORDER BY cr.service_id"""
    )
    return [_row_to_dict(r) for r in rows]


async def get_last_check_timestamp() -> Optional[str]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT MAX(checked_at) AS last_check_at FROM check_results")
    return rows[0]["last_check_at"] if rows and rows[0]["last_check_at"] else None


async def count_check_results_since(hours: int = 24) -> int:
    db = await get_db()
    cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    rows = await db.execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM check_results WHERE checked_at >= ?",
        (cutoff,),
    )
    return int(rows[0]["cnt"] if rows else 0)


async def count_failed_checks_since(hours: int = 24) -> int:
    db = await get_db()
    cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    rows = await db.execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM check_results WHERE checked_at >= ? AND status != 'ok'",
        (cutoff,),
    )
    return int(rows[0]["cnt"] if rows else 0)


# ─── Alerts ───────────────────────────────────────────────


async def create_alert(
    service_id: int,
    alert_type: str,
    message: str,
    severity: str,
) -> dict:
    db = await get_db()
    cursor = await db.execute(
        """INSERT INTO alerts (service_id, alert_type, message, severity, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (service_id, alert_type, message, severity, _now()),
    )
    await db.commit()
    rows = await db.execute_fetchall("SELECT * FROM alerts WHERE id = ?", (cursor.lastrowid,))
    return _row_to_dict(rows[0])


async def get_alert(alert_id: int) -> Optional[dict]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM alerts WHERE id = ?", (alert_id,))
    return _row_to_dict(rows[0]) if rows else None


async def list_alerts(
    service_id: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    db = await get_db()
    if service_id is not None:
        rows = await db.execute_fetchall(
            "SELECT * FROM alerts WHERE service_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (service_id, limit, offset),
        )
    else:
        rows = await db.execute_fetchall(
            "SELECT * FROM alerts ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
    return [_row_to_dict(r) for r in rows]


async def acknowledge_alert(alert_id: int) -> Optional[dict]:
    db = await get_db()
    await db.execute(
        "UPDATE alerts SET acknowledged = 1, resolved_at = ? WHERE id = ?",
        (_now(), alert_id),
    )
    await db.commit()
    return await get_alert(alert_id)


async def resolve_alert(alert_id: int) -> Optional[dict]:
    """Manually resolve an alert without acknowledging workflow."""
    db = await get_db()
    await db.execute(
        "UPDATE alerts SET resolved_at = ? WHERE id = ?",
        (_now(), alert_id),
    )
    await db.commit()
    return await get_alert(alert_id)


async def count_active_alerts() -> int:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM alerts WHERE resolved_at IS NULL"
    )
    return rows[0]["cnt"]


async def list_alerts_for_host(host_id: int, limit: int = 50) -> list[dict]:
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT a.* FROM alerts a
           JOIN services s ON s.id = a.service_id
           WHERE s.host_id = ?
           ORDER BY a.created_at DESC LIMIT ?""",
        (host_id, limit),
    )
    return [_row_to_dict(r) for r in rows]


async def get_recent_alerts(limit: int = 10) -> list[dict]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)
    )
    return [_row_to_dict(r) for r in rows]


# ─── State Log ────────────────────────────────────────────


async def insert_state_change(
    service_id: int,
    old_state: str,
    new_state: str,
    reason: str = "",
) -> dict:
    db = await get_db()
    cursor = await db.execute(
        """INSERT INTO state_log (service_id, old_state, new_state, reason, changed_at)
           VALUES (?, ?, ?, ?, ?)""",
        (service_id, old_state, new_state, reason, _now()),
    )
    await db.commit()
    rows = await db.execute_fetchall("SELECT * FROM state_log WHERE id = ?", (cursor.lastrowid,))
    return _row_to_dict(rows[0])


async def list_state_changes(
    service_id: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    db = await get_db()
    if service_id is not None:
        rows = await db.execute_fetchall(
            "SELECT * FROM state_log WHERE service_id = ? ORDER BY changed_at DESC LIMIT ? OFFSET ?",
            (service_id, limit, offset),
        )
    else:
        rows = await db.execute_fetchall(
            "SELECT * FROM state_log ORDER BY changed_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
    return [_row_to_dict(r) for r in rows]


# Audit Log


async def create_audit_log(
    action: str,
    *,
    actor: str = "system",
    resource: str = "",
    resource_id: str | int | None = None,
    detail: str = "",
    ip_address: str = "",
) -> dict:
    db = await get_db()
    cursor = await db.execute(
        """INSERT INTO audit_logs
           (actor, action, resource, resource_id, detail, ip_address, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            actor or "system",
            action,
            resource,
            "" if resource_id is None else str(resource_id),
            detail,
            ip_address,
            _now(),
        ),
    )
    await db.commit()
    rows = await db.execute_fetchall("SELECT * FROM audit_logs WHERE id = ?", (cursor.lastrowid,))
    return _row_to_dict(rows[0])


async def list_audit_logs(
    limit: int = 100,
    offset: int = 0,
    actor: Optional[str] = None,
    action: Optional[str] = None,
) -> list[dict]:
    db = await get_db()
    conditions = []
    params: list[Any] = []
    if actor:
        conditions.append("actor = ?")
        params.append(actor)
    if action:
        conditions.append("action = ?")
        params.append(action)
    query = "SELECT * FROM audit_logs"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = await db.execute_fetchall(query, params)
    return [_row_to_dict(r) for r in rows]


# System Logs


async def create_system_log(
    *,
    level: str,
    category: str,
    event_type: str,
    message: str,
    source: str = "",
    entity_type: str = "",
    entity_id: str = "",
    user_id: str = "",
    ip_address: str = "",
    metadata_json: str = "{}",
    is_sensitive: int = 0,
) -> dict:
    db = await get_db()
    cursor = await db.execute(
        """INSERT INTO system_logs
           (level, category, event_type, message, source, entity_type, entity_id,
            user_id, ip_address, metadata_json, is_sensitive, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            level,
            category,
            event_type,
            message,
            source,
            entity_type,
            entity_id,
            user_id,
            ip_address,
            metadata_json,
            int(is_sensitive),
            _now(),
        ),
    )
    await db.commit()
    rows = await db.execute_fetchall("SELECT * FROM system_logs WHERE id = ?", (cursor.lastrowid,))
    return _row_to_dict(rows[0])


async def get_system_log(log_id: int) -> Optional[dict]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM system_logs WHERE id = ?", (log_id,))
    return _row_to_dict(rows[0]) if rows else None


async def list_system_logs(
    *,
    category: Optional[str] = None,
    level: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    errors_only: bool = False,
    since_id: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    db = await get_db()
    conditions: list[str] = []
    params: list[Any] = []
    if since_id is not None and since_id > 0:
        conditions.append("id > ?")
        params.append(int(since_id))
    if category:
        conditions.append("category = ?")
        params.append(category)
    if level:
        conditions.append("level = ?")
        params.append(level)
    if errors_only:
        conditions.append("level IN ('error', 'critical')")
    if search:
        like = f"%{search}%"
        conditions.append("(message LIKE ? OR event_type LIKE ? OR source LIKE ?)")
        params.extend([like, like, like])
    if date_from:
        conditions.append("created_at >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("created_at <= ?")
        params.append(date_to)

    query = "SELECT * FROM system_logs"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    if since_id is not None and since_id > 0:
        query += " ORDER BY id ASC LIMIT ?"
        params.append(limit)
    else:
        query += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    rows = await db.execute_fetchall(query, params)
    return [_row_to_dict(r) for r in rows]


async def get_last_system_log(*, levels: Optional[list[str]] = None) -> Optional[dict]:
    db = await get_db()
    params: list[Any] = []
    query = "SELECT * FROM system_logs"
    if levels:
        placeholders = ", ".join("?" for _ in levels)
        query += f" WHERE level IN ({placeholders})"
        params.extend(levels)
    query += " ORDER BY created_at DESC, id DESC LIMIT 1"
    rows = await db.execute_fetchall(query, params)
    return _row_to_dict(rows[0]) if rows else None


async def get_last_system_log_timestamp(*, levels: Optional[list[str]] = None) -> Optional[str]:
    row = await get_last_system_log(levels=levels)
    return str(row.get("created_at")) if row else None


async def count_system_logs_since(
    *,
    hours: int = 24,
    level: Optional[str] = None,
    levels: Optional[list[str]] = None,
    category: Optional[str] = None,
    event_type: Optional[str] = None,
) -> int:
    db = await get_db()
    cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    conditions = ["created_at >= ?"]
    params: list[Any] = [cutoff]
    if level:
        conditions.append("level = ?")
        params.append(level)
    if levels:
        placeholders = ", ".join("?" for _ in levels)
        conditions.append(f"level IN ({placeholders})")
        params.extend(levels)
    if category:
        conditions.append("category = ?")
        params.append(category)
    if event_type:
        conditions.append("event_type = ?")
        params.append(event_type)
    rows = await db.execute_fetchall(
        f"SELECT COUNT(*) AS cnt FROM system_logs WHERE {' AND '.join(conditions)}",
        params,
    )
    return int(rows[0]["cnt"] if rows else 0)


# App Settings


async def set_app_setting(key: str, value: str) -> dict:
    db = await get_db()
    now = _now()
    await db.execute(
        """INSERT INTO app_settings (key, value, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
        (key, value, now),
    )
    await db.commit()
    rows = await db.execute_fetchall("SELECT * FROM app_settings WHERE key = ?", (key,))
    return _row_to_dict(rows[0])


async def get_app_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT value FROM app_settings WHERE key = ?", (key,))
    return rows[0]["value"] if rows else default


async def set_maintenance_mode(enabled: bool) -> dict:
    return await set_app_setting("maintenance_mode", "1" if enabled else "0")


async def get_maintenance_mode(default: bool = False) -> bool:
    value = await get_app_setting("maintenance_mode", "1" if default else "0")
    return str(value).lower() in {"1", "true", "yes", "on"}


# Reports


async def list_report_check_rows(
    service_id: Optional[int] = None,
    host_id: Optional[int] = None,
    status: Optional[str] = None,
    limit: int = 10000,
) -> list[dict]:
    db = await get_db()
    query = """SELECT cr.*, s.name AS service_name, s.check_type, s.host_id,
                      h.name AS host_name, h.address AS host_address
               FROM check_results cr
               JOIN services s ON s.id = cr.service_id
               JOIN hosts h ON h.id = s.host_id"""
    conditions = []
    params: list[Any] = []
    if service_id is not None:
        conditions.append("cr.service_id = ?")
        params.append(service_id)
    if host_id is not None:
        conditions.append("s.host_id = ?")
        params.append(host_id)
    if status is not None:
        conditions.append("cr.status = ?")
        params.append(status)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY cr.checked_at DESC LIMIT ?"
    params.append(limit)
    rows = await db.execute_fetchall(query, params)
    return [_row_to_dict(r) for r in rows]


# ─── Dashboard Queries ────────────────────────────────────


async def get_dashboard_stats() -> dict:
    db = await get_db()
    hosts = await db.execute_fetchall("SELECT COUNT(*) AS cnt FROM hosts")
    services = await db.execute_fetchall("SELECT COUNT(*) AS cnt FROM services")
    states = await db.execute_fetchall(
        "SELECT current_state, COUNT(*) AS cnt FROM services GROUP BY current_state"
    )
    state_map = {r["current_state"]: r["cnt"] for r in states}

    hosts_status = await get_hosts_status()
    hosts_up = sum(1 for h in hosts_status if h["overall_state"] == "ok")
    hosts_down = sum(
        1 for h in hosts_status
        if h["overall_state"] in ("warning", "critical")
    )

    return {
        "total_hosts": hosts[0]["cnt"],
        "hosts_up": hosts_up,
        "hosts_down": hosts_down,
        "total_services": services[0]["cnt"],
        "services_ok": state_map.get("ok", 0),
        "services_warning": state_map.get("warning", 0),
        "services_critical": state_map.get("critical", 0),
        "services_unknown": state_map.get("unknown", 0),
        "active_alerts": await count_active_alerts(),
    }


async def get_hosts_status() -> list[dict]:
    """All hosts with their services for the status dashboard."""
    db = await get_db()
    hosts = await db.execute_fetchall("SELECT * FROM hosts ORDER BY id")
    result = []
    for h in hosts:
        host_dict = _row_to_dict(h)
        svcs = await db.execute_fetchall(
            "SELECT * FROM services WHERE host_id = ? ORDER BY id", (h["id"],)
        )
        svc_list = [_row_to_dict(s) for s in svcs]
        # Determine overall state (worst state wins)
        priority = {"critical": 3, "warning": 2, "unknown": 1, "ok": 0}
        worst = "ok" if svc_list else "unknown"
        for s in svc_list:
            if priority.get(s["current_state"], 0) > priority.get(worst, 0):
                worst = s["current_state"]
        result.append({
            "host": host_dict,
            "services": svc_list,
            "overall_state": worst,
        })
    return result


# Device inventory


def _host_inventory_state(row: dict[str, Any]) -> str:
    """Collapse service and linked-agent health into one NOC-friendly state."""
    if not row.get("enabled"):
        return "disabled"
    if row.get("active_alerts", 0) > 0 or row.get("services_critical", 0) > 0:
        return "critical"
    if row.get("services_warning", 0) > 0:
        return "warning"
    if row.get("services_total", 0) == 0 and row.get("agent_count", 0) == 0:
        return "unknown"
    if row.get("services_unknown", 0) > 0:
        return "unknown"
    if row.get("agent_count", 0) > 0 and row.get("agents_online", 0) == 0:
        return "warning"
    return "ok"


def _agent_inventory_state(row: dict[str, Any]) -> str:
    if not row.get("enabled"):
        return "disabled"
    if row.get("active_alerts", 0) > 0:
        return "critical"
    status = str(row.get("status") or "pending").lower()
    if status == "online":
        return "ok"
    if status in {"offline", "stale", "down"}:
        return "critical"
    return "unknown"


async def get_device_inventory() -> dict[str, Any]:
    """Return a compact inventory view for hosts and standalone agents."""
    db = await get_db()
    host_rows = await db.execute_fetchall(
        """
        SELECT
            h.*,
            COALESCE(sc.services_total, 0) AS services_total,
            COALESCE(sc.services_ok, 0) AS services_ok,
            COALESCE(sc.services_warning, 0) AS services_warning,
            COALESCE(sc.services_critical, 0) AS services_critical,
            COALESCE(sc.services_unknown, 0) AS services_unknown,
            COALESCE(ac.active_alerts, 0) AS active_alerts,
            lc.last_check_at,
            COALESCE(ag.agent_count, 0) AS agent_count,
            COALESCE(ag.agents_online, 0) AS agents_online,
            ag.last_heartbeat_at,
            ag.last_metrics_at
        FROM hosts h
        LEFT JOIN (
            SELECT
                host_id,
                COUNT(*) AS services_total,
                SUM(CASE WHEN current_state = 'ok' THEN 1 ELSE 0 END) AS services_ok,
                SUM(CASE WHEN current_state = 'warning' THEN 1 ELSE 0 END) AS services_warning,
                SUM(CASE WHEN current_state = 'critical' THEN 1 ELSE 0 END) AS services_critical,
                SUM(CASE WHEN current_state = 'unknown' THEN 1 ELSE 0 END) AS services_unknown
            FROM services
            GROUP BY host_id
        ) sc ON sc.host_id = h.id
        LEFT JOIN (
            SELECT
                s.host_id,
                COUNT(a.id) AS active_alerts
            FROM alerts a
            JOIN services s ON s.id = a.service_id
            WHERE a.resolved_at IS NULL
            GROUP BY s.host_id
        ) ac ON ac.host_id = h.id
        LEFT JOIN (
            SELECT
                s.host_id,
                MAX(cr.checked_at) AS last_check_at
            FROM check_results cr
            JOIN services s ON s.id = cr.service_id
            GROUP BY s.host_id
        ) lc ON lc.host_id = h.id
        LEFT JOIN (
            SELECT
                host_id,
                COUNT(*) AS agent_count,
                SUM(CASE WHEN status = 'online' THEN 1 ELSE 0 END) AS agents_online,
                MAX(last_heartbeat_at) AS last_heartbeat_at,
                MAX(last_metrics_at) AS last_metrics_at
            FROM agents
            WHERE host_id IS NOT NULL
            GROUP BY host_id
        ) ag ON ag.host_id = h.id
        ORDER BY h.id
        """
    )
    standalone_agent_rows = await db.execute_fetchall(
        """
        SELECT
            a.*,
            COALESCE(aa.active_alerts, 0) AS active_alerts,
            m.cpu_percent,
            m.memory_percent,
            m.disk_percent,
            m.collected_at AS metrics_collected_at
        FROM agents a
        LEFT JOIN (
            SELECT agent_id, COUNT(*) AS active_alerts
            FROM agent_alerts
            WHERE resolved_at IS NULL
            GROUP BY agent_id
        ) aa ON aa.agent_id = a.id
        LEFT JOIN (
            SELECT am.*
            FROM agent_metrics am
            JOIN (
                SELECT agent_id, MAX(collected_at) AS max_collected_at
                FROM agent_metrics
                GROUP BY agent_id
            ) latest
              ON latest.agent_id = am.agent_id
             AND latest.max_collected_at = am.collected_at
        ) m ON m.agent_id = a.id
        WHERE a.host_id IS NULL
        ORDER BY a.id
        """
    )

    devices: list[dict[str, Any]] = []
    for row in host_rows:
        item = _row_to_dict(row)
        state = _host_inventory_state(item)
        devices.append(
            {
                "device_id": f"host:{item['id']}",
                "source": "host",
                "id": item["id"],
                "name": item["name"],
                "address": item["address"],
                "hostname": item.get("hostname") or "",
                "mac_address": item.get("mac_address") or "",
                "device_type": item.get("host_type") or "server",
                "location": item.get("location") or "",
                "tags": item.get("tags") or "",
                "enabled": bool(item.get("enabled")),
                "state": state,
                "services_total": item["services_total"],
                "services_ok": item["services_ok"],
                "services_warning": item["services_warning"],
                "services_critical": item["services_critical"],
                "services_unknown": item["services_unknown"],
                "active_alerts": item["active_alerts"],
                "agent_count": item["agent_count"],
                "agents_online": item["agents_online"],
                "last_check_at": item.get("last_check_at"),
                "last_heartbeat_at": item.get("last_heartbeat_at"),
                "last_metrics_at": item.get("last_metrics_at"),
                "detail_url": f"/devices/host-{item['id']}",
            }
        )

    for row in standalone_agent_rows:
        item = _row_to_dict(row)
        state = _agent_inventory_state(item)
        devices.append(
            {
                "device_id": f"agent:{item['id']}",
                "source": "agent",
                "id": item["id"],
                "name": item["name"],
                "address": item.get("hostname") or "",
                "device_type": "agent",
                "location": "",
                "tags": "",
                "enabled": bool(item.get("enabled")),
                "state": state,
                "services_total": 0,
                "services_ok": 0,
                "services_warning": 0,
                "services_critical": 0,
                "services_unknown": 0,
                "active_alerts": item["active_alerts"],
                "agent_count": 1,
                "agents_online": 1 if item.get("status") == "online" else 0,
                "last_check_at": None,
                "last_heartbeat_at": item.get("last_heartbeat_at"),
                "last_metrics_at": item.get("last_metrics_at"),
                "metrics": {
                    "cpu_percent": item.get("cpu_percent"),
                    "memory_percent": item.get("memory_percent"),
                    "disk_percent": item.get("disk_percent"),
                    "collected_at": item.get("metrics_collected_at"),
                },
                "detail_url": f"/api/v1/agents/{item['id']}",
            }
        )

    summary = {
        "total": len(devices),
        "ok": sum(1 for item in devices if item["state"] == "ok"),
        "warning": sum(1 for item in devices if item["state"] == "warning"),
        "critical": sum(1 for item in devices if item["state"] == "critical"),
        "unknown": sum(1 for item in devices if item["state"] == "unknown"),
        "disabled": sum(1 for item in devices if item["state"] == "disabled"),
        "hosts": len(host_rows),
        "standalone_agents": len(standalone_agent_rows),
    }
    return {"summary": summary, "devices": devices}


# ─── Agents / Metrics ─────────────────────────────────────


async def create_agent(
    name: str,
    token_hash: str,
    *,
    hostname: str = "",
    host_id: Optional[int] = None,
    status: str = "pending",
) -> dict:
    db = await get_db()
    cursor = await db.execute(
        """INSERT INTO agents
           (name, hostname, host_id, token_hash, status, registered_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (name, hostname, host_id, token_hash, status, _now()),
    )
    await db.commit()
    rows = await db.execute_fetchall("SELECT * FROM agents WHERE id = ?", (cursor.lastrowid,))
    return _row_to_dict(rows[0])


async def get_agent(agent_id: int) -> Optional[dict]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM agents WHERE id = ?", (agent_id,))
    return _row_to_dict(rows[0]) if rows else None


async def get_agent_by_token_hash(token_hash: str) -> Optional[dict]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM agents WHERE token_hash = ? AND enabled = 1",
        (token_hash,),
    )
    return _row_to_dict(rows[0]) if rows else None


async def list_agents() -> list[dict]:
    db = await get_db()
    rows = await db.execute_fetchall("SELECT * FROM agents ORDER BY id")
    return [_row_to_dict(r) for r in rows]


async def update_agent(agent_id: int, **fields) -> Optional[dict]:
    db = await get_db()
    allowed = (
        "name", "hostname", "host_id", "status", "last_heartbeat_at",
        "last_metrics_at", "enabled",
    )
    sets, vals = [], []
    for key in allowed:
        if key in fields and fields[key] is not None:
            val = fields[key]
            if key == "enabled":
                val = int(val)
            sets.append(f"{key} = ?")
            vals.append(val)
    if not sets:
        return await get_agent(agent_id)
    sets.append("updated_at = ?")
    vals.append(_now())
    vals.append(agent_id)
    await db.execute(f"UPDATE agents SET {', '.join(sets)} WHERE id = ?", vals)
    await db.commit()
    return await get_agent(agent_id)


async def insert_agent_metrics(
    agent_id: int,
    cpu_percent: float,
    memory_percent: float,
    disk_percent: float,
) -> dict:
    db = await get_db()
    now = _now()
    cursor = await db.execute(
        """INSERT INTO agent_metrics
           (agent_id, cpu_percent, memory_percent, disk_percent, collected_at)
           VALUES (?, ?, ?, ?, ?)""",
        (agent_id, cpu_percent, memory_percent, disk_percent, now),
    )
    await db.execute(
        "UPDATE agents SET last_metrics_at = ?, updated_at = ? WHERE id = ?",
        (now, now, agent_id),
    )
    await db.commit()
    rows = await db.execute_fetchall(
        "SELECT * FROM agent_metrics WHERE id = ?", (cursor.lastrowid,)
    )
    return _row_to_dict(rows[0])


async def list_agent_metrics(agent_id: int, limit: int = 100) -> list[dict]:
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM agent_metrics WHERE agent_id = ? ORDER BY collected_at DESC LIMIT ?",
        (agent_id, limit),
    )
    return [_row_to_dict(r) for r in rows]


async def create_agent_alert(
    agent_id: int,
    alert_type: str,
    message: str,
    severity: str,
    *,
    metric_name: str = "",
    metric_value: Optional[float] = None,
    threshold: Optional[float] = None,
) -> dict:
    db = await get_db()
    cursor = await db.execute(
        """INSERT INTO agent_alerts
           (agent_id, alert_type, message, severity, metric_name, metric_value, threshold, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            agent_id,
            alert_type,
            message,
            severity,
            metric_name,
            metric_value,
            threshold,
            _now(),
        ),
    )
    await db.commit()
    rows = await db.execute_fetchall("SELECT * FROM agent_alerts WHERE id = ?", (cursor.lastrowid,))
    return _row_to_dict(rows[0])


async def list_agent_alerts(
    agent_id: Optional[int] = None,
    active_only: bool = False,
    limit: int = 100,
) -> list[dict]:
    db = await get_db()
    query = "SELECT * FROM agent_alerts"
    conditions = []
    params: list[Any] = []
    if agent_id is not None:
        conditions.append("agent_id = ?")
        params.append(agent_id)
    if active_only:
        conditions.append("resolved_at IS NULL")
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = await db.execute_fetchall(query, params)
    return [_row_to_dict(r) for r in rows]


async def get_active_agent_alert(agent_id: int, alert_type: str) -> Optional[dict]:
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT * FROM agent_alerts
           WHERE agent_id = ? AND alert_type = ? AND resolved_at IS NULL
           ORDER BY created_at DESC LIMIT 1""",
        (agent_id, alert_type),
    )
    return _row_to_dict(rows[0]) if rows else None


async def resolve_agent_alert(alert_id: int) -> Optional[dict]:
    db = await get_db()
    await db.execute(
        "UPDATE agent_alerts SET resolved_at = ? WHERE id = ?",
        (_now(), alert_id),
    )
    await db.commit()
    rows = await db.execute_fetchall("SELECT * FROM agent_alerts WHERE id = ?", (alert_id,))
    return _row_to_dict(rows[0]) if rows else None


async def list_stale_agents(cutoff_iso: str) -> list[dict]:
    """Agents that were online but missed heartbeat beyond the cutoff timestamp."""
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT * FROM agents
           WHERE enabled = 1
             AND status = 'online'
             AND last_heartbeat_at IS NOT NULL
             AND last_heartbeat_at < ?""",
        (cutoff_iso,),
    )
    return [_row_to_dict(r) for r in rows]


# ─── Data Retention ───────────────────────────────────────


async def purge_old_data(
    *,
    result_retention_days: int | None = None,
    alert_retention_days: int | None = None,
    metric_retention_days: int | None = None,
) -> int:
    """Delete old check results, resolved alerts, metrics, and state log rows."""
    result_days = result_retention_days if result_retention_days is not None else settings.result_retention_days
    alert_days = alert_retention_days if alert_retention_days is not None else settings.alert_retention_days
    metric_days = metric_retention_days if metric_retention_days is not None else settings.metric_retention_days

    total = 0
    db_conn = await get_db()
    now = datetime.now(UTC)

    if result_days > 0:
        cutoff = (now - timedelta(days=result_days)).isoformat()
        c1 = await db_conn.execute("DELETE FROM check_results WHERE checked_at < ?", (cutoff,))
        c3 = await db_conn.execute("DELETE FROM state_log WHERE changed_at < ?", (cutoff,))
        total += c1.rowcount + c3.rowcount

    if alert_days > 0:
        cutoff = (now - timedelta(days=alert_days)).isoformat()
        c2 = await db_conn.execute(
            "DELETE FROM alerts WHERE resolved_at IS NOT NULL AND created_at < ?", (cutoff,)
        )
        total += c2.rowcount

    if metric_days > 0:
        cutoff = (now - timedelta(days=metric_days)).isoformat()
        c4 = await db_conn.execute("DELETE FROM agent_metrics WHERE collected_at < ?", (cutoff,))
        total += c4.rowcount

    if total:
        await db_conn.commit()
        logger.info("Retention purge removed {} records", total)
    return total


# ─── Licensing ────────────────────────────────────────────


async def get_active_license() -> Optional[dict]:
    db_conn = await get_db()
    rows = await db_conn.execute_fetchall(
        """SELECT * FROM licenses
           WHERE status IN ('active', 'grace', 'expired', 'trial')
           ORDER BY id DESC LIMIT 1"""
    )
    return _row_to_dict(rows[0]) if rows else None


async def deactivate_all_licenses() -> None:
    db_conn = await get_db()
    await db_conn.execute(
        """UPDATE licenses SET status = 'replaced', updated_at = ?
           WHERE status IN ('active', 'trial', 'grace')""",
        (_now(),),
    )
    await db_conn.commit()


async def get_license_by_key_hash(license_key_hash: str) -> Optional[dict]:
    """Return any license row that was activated with this code hash."""
    key_hash = str(license_key_hash or "").strip()
    if not key_hash:
        return None
    db_conn = await get_db()
    rows = await db_conn.execute_fetchall(
        "SELECT * FROM licenses WHERE license_key_hash = ? ORDER BY id DESC LIMIT 1",
        (key_hash,),
    )
    return _row_to_dict(rows[0]) if rows else None


async def update_license_status(license_id: int, status: str, *, limits_json: str | None = None) -> None:
    db_conn = await get_db()
    if limits_json is not None:
        await db_conn.execute(
            "UPDATE licenses SET status = ?, limits_json = ?, updated_at = ? WHERE id = ?",
            (status, limits_json, _now(), license_id),
        )
    else:
        await db_conn.execute(
            "UPDATE licenses SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), license_id),
        )
    await db_conn.commit()


async def create_license(
    *,
    license_key_hash: str,
    tier: str,
    owner_name: str = "",
    expires_at: str | None = None,
    status: str = "active",
    limits_json: str = "{}",
    signature_valid: int = 1,
) -> dict:
    db_conn = await get_db()
    now = _now()
    cursor = await db_conn.execute(
        """INSERT INTO licenses
           (license_key_hash, tier, owner_name, expires_at, activated_at,
            last_validated_at, status, limits_json, signature_valid, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            license_key_hash,
            tier,
            owner_name,
            expires_at,
            now,
            now,
            status,
            limits_json,
            signature_valid,
            now,
        ),
    )
    await db_conn.commit()
    rows = await db_conn.execute_fetchall(
        "SELECT * FROM licenses WHERE id = ?", (cursor.lastrowid,)
    )
    return _row_to_dict(rows[0])


# ─── Discovery ────────────────────────────────────────────


async def create_discovery_scan(profile: str, subnets_json: str, *, request_id: str = "") -> dict:
    db_conn = await get_db()
    cursor = await db_conn.execute(
        """INSERT INTO discovery_scans
           (profile, subnets_json, status, request_id, created_at)
           VALUES (?, ?, 'pending', ?, ?)""",
        (profile, subnets_json, request_id, _now()),
    )
    await db_conn.commit()
    rows = await db_conn.execute_fetchall(
        "SELECT * FROM discovery_scans WHERE id = ?", (cursor.lastrowid,)
    )
    return _row_to_dict(rows[0])


async def get_discovery_scan(scan_id: int) -> Optional[dict]:
    db_conn = await get_db()
    rows = await db_conn.execute_fetchall(
        "SELECT * FROM discovery_scans WHERE id = ?", (scan_id,)
    )
    return _row_to_dict(rows[0]) if rows else None


async def get_discovery_scan_events(
    scan_id: int,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Discovery scan timeline events stored in system_logs."""
    db_conn = await get_db()
    rows = await db_conn.execute_fetchall(
        """SELECT * FROM system_logs
           WHERE category = 'discovery'
             AND entity_type = 'scan'
             AND entity_id = ?
           ORDER BY created_at DESC, id DESC
           LIMIT ? OFFSET ?""",
        (str(scan_id), limit, offset),
    )
    events: list[dict] = []
    for row in rows:
        item = _row_to_dict(row)
        item["timestamp"] = item.get("created_at")
        events.append(item)
    return events


async def list_discovery_scans(limit: int = 50) -> list[dict]:
    db_conn = await get_db()
    rows = await db_conn.execute_fetchall(
        "SELECT * FROM discovery_scans ORDER BY id DESC LIMIT ?", (limit,)
    )
    return [_row_to_dict(r) for r in rows]


async def mark_interrupted_discovery_scans() -> int:
    """Mark scans left pending/running by a previous process as failed."""
    db_conn = await get_db()
    now = _now()
    cursor = await db_conn.execute(
        """UPDATE discovery_scans
           SET status = 'failed',
               error_message = COALESCE(error_message, ?),
               finished_at = COALESCE(finished_at, ?)
           WHERE status IN ('pending', 'running')""",
        ("Discovery scan interrupted by application restart.", now),
    )
    await db_conn.commit()
    return int(cursor.rowcount or 0)


async def update_discovery_scan(scan_id: int, **fields) -> Optional[dict]:
    db_conn = await get_db()
    allowed = {
        "status",
        "subnets_json",
        "progress_percent",
        "total_hosts",
        "scanned_hosts",
        "found_count",
        "failed_probe_count",
        "current_ip",
        "current_subnet",
        "current_stage",
        "stage_message",
        "elapsed_seconds",
        "probe_methods_json",
        "diagnostics_json",
        "diagnostic_meta_json",
        "permission_errors_json",
        "request_id",
        "error_message",
        "started_at",
        "finished_at",
    }
    sets, vals = [], []
    for key, val in fields.items():
        if key in allowed and val is not None:
            sets.append(f"{key} = ?")
            vals.append(val)
    if not sets:
        return await get_discovery_scan(scan_id)
    if fields.get("status") == "running":
        sets.append("started_at = COALESCE(started_at, ?)")
        vals.append(_now())
    if fields.get("status") in ("completed", "cancelled", "failed"):
        sets.append("finished_at = ?")
        vals.append(_now())
    vals.append(scan_id)
    await db_conn.execute(
        f"UPDATE discovery_scans SET {', '.join(sets)} WHERE id = ?", vals
    )
    await db_conn.commit()
    return await get_discovery_scan(scan_id)


async def upsert_discovered_device(
    *,
    scan_id: int,
    ip_address: str,
    mac_address: str = "",
    hostname: str = "",
    vendor: str = "",
    open_ports: str = "[]",
    detected_services: str = "[]",
    detected_type: str = "unknown",
    confidence: int = 0,
    discovery_source: str = "",
    raw_metadata_json: str = "{}",
) -> dict:
    db_conn = await get_db()
    now = _now()
    await db_conn.execute(
        """INSERT INTO discovered_devices
           (scan_id, ip_address, mac_address, hostname, vendor, open_ports,
            detected_services, detected_type, confidence, first_seen_at,
            last_seen_at, discovery_source, raw_metadata_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(scan_id, ip_address) DO UPDATE SET
             mac_address = excluded.mac_address,
             hostname = excluded.hostname,
             vendor = excluded.vendor,
             open_ports = excluded.open_ports,
             detected_services = excluded.detected_services,
             detected_type = excluded.detected_type,
             confidence = excluded.confidence,
             last_seen_at = excluded.last_seen_at,
             discovery_source = excluded.discovery_source,
             raw_metadata_json = excluded.raw_metadata_json""",
        (
            scan_id,
            ip_address,
            mac_address,
            hostname,
            vendor,
            open_ports,
            detected_services,
            detected_type,
            confidence,
            now,
            now,
            discovery_source,
            raw_metadata_json,
        ),
    )
    await db_conn.commit()
    rows = await db_conn.execute_fetchall(
        "SELECT * FROM discovered_devices WHERE scan_id = ? AND ip_address = ?",
        (scan_id, ip_address),
    )
    return _row_to_dict(rows[0])


async def list_discovered_devices(
    *,
    scan_id: int | None = None,
    limit: int = 500,
    hide_demo: bool = True,
    monitored_subnets: list[str] | None = None,
) -> list[dict]:
    """List discovered devices. Without scan_id, only authorized monitored subnets are shown."""
    from ditaknet.discovery.store import DEMO_DISCOVERY_SOURCES, filter_devices_by_monitored_subnets

    db_conn = await get_db()
    demo_clause = ""
    demo_params: list[Any] = []
    if hide_demo:
        placeholders = ", ".join("?" for _ in DEMO_DISCOVERY_SOURCES)
        demo_clause = f" AND LOWER(discovery_source) NOT IN ({placeholders})"
        demo_params = list(DEMO_DISCOVERY_SOURCES)
    if scan_id is not None:
        rows = await db_conn.execute_fetchall(
            f"""SELECT * FROM discovered_devices
               WHERE scan_id = ?{demo_clause}
               ORDER BY confidence DESC, ip_address
               LIMIT ?""",
            (scan_id, *demo_params, limit),
        )
        return [_row_to_dict(r) for r in rows]
    if monitored_subnets is None:
        from ditaknet.discovery.store import list_monitored_networks

        monitored_subnets = [
            str(n.get("cidr") or "")
            for n in await list_monitored_networks(enabled_only=True)
            if n.get("cidr")
        ]
    if not monitored_subnets:
        return []
    rows = await db_conn.execute_fetchall(
        f"""SELECT * FROM discovered_devices
           WHERE 1=1{demo_clause}
           ORDER BY last_seen_at DESC
           LIMIT ?""",
        (*demo_params, limit * 5),
    )
    devices = [_row_to_dict(r) for r in rows]
    return filter_devices_by_monitored_subnets(devices, monitored_subnets, limit=limit)


async def get_discovered_device(device_id: int) -> Optional[dict]:
    db_conn = await get_db()
    rows = await db_conn.execute_fetchall(
        "SELECT * FROM discovered_devices WHERE id = ?", (device_id,)
    )
    return _row_to_dict(rows[0]) if rows else None


async def mark_discovered_device_imported(device_id: int, host_id: int) -> None:
    db_conn = await get_db()
    rows = await db_conn.execute_fetchall(
        "SELECT ip_address FROM discovered_devices WHERE id = ?", (device_id,)
    )
    ip_address = ""
    if rows:
        row = rows[0]
        ip_address = str(row["ip_address"] if isinstance(row, dict) else row[0] or "")
    await db_conn.execute(
        "UPDATE discovered_devices SET imported_host_id = ? WHERE id = ?",
        (host_id, device_id),
    )
    if ip_address:
        await db_conn.execute(
            """UPDATE discovered_devices
               SET imported_host_id = ?
               WHERE ip_address = ? AND imported_host_id IS NULL""",
            (host_id, ip_address),
        )
    await db_conn.commit()


async def count_pending_discovery_imports() -> int:
    """Discovered devices not yet imported as monitored hosts."""
    db_conn = await get_db()
    rows = await db_conn.execute_fetchall(
        "SELECT COUNT(*) AS c FROM discovered_devices WHERE imported_host_id IS NULL"
    )
    if not rows:
        return 0
    row = rows[0]
    if isinstance(row, dict):
        return int(row.get("c") or 0)
    return int(row[0])


async def list_pending_discovered_inventory(*, limit: int = 500) -> list[dict]:
    """Unimported discovery rows deduped by IP for the Connected Devices page."""
    devices = await list_discovered_devices(limit=max(limit * 2, 100))
    pending = [d for d in devices if not d.get("imported_host_id")]
    by_ip: dict[str, dict] = {}
    for device in pending:
        ip = str(device.get("ip_address") or "").strip()
        if not ip:
            continue
        current = by_ip.get(ip)
        if not current or str(device.get("last_seen_at") or "") > str(current.get("last_seen_at") or ""):
            by_ip[ip] = device
    ordered = sorted(by_ip.values(), key=lambda item: str(item.get("last_seen_at") or ""), reverse=True)
    return ordered[:limit]


def discovered_device_inventory_item(device: dict) -> dict:
    """Map a discovered_devices row to the shared device inventory shape."""
    from ditaknet.discovery.naming import resolve_device_name_from_record

    device_id = int(device["id"])
    scan_id = int(device.get("scan_id") or 0)
    name = resolve_device_name_from_record(device)
    return {
        "device_id": f"discovered:{device_id}",
        "source": "discovered",
        "id": device_id,
        "name": name,
        "display_name": name,
        "address": str(device.get("ip_address") or ""),
        "hostname": str(device.get("hostname") or ""),
        "vendor": str(device.get("vendor") or ""),
        "device_type": str(device.get("detected_type") or "unknown"),
        "location": "",
        "tags": "discovered",
        "enabled": True,
        "state": "pending",
        "services_total": 0,
        "services_ok": 0,
        "services_warning": 0,
        "services_critical": 0,
        "services_unknown": 0,
        "active_alerts": 0,
        "agent_count": 0,
        "agents_online": 0,
        "last_check_at": None,
        "last_heartbeat_at": None,
        "last_metrics_at": device.get("last_seen_at"),
        "scan_id": scan_id,
        "detail_url": f"/discovery?tab=results&scan_id={scan_id}" if scan_id else "/discovery?tab=results",
        "import_url": f"/discovery/import?scan_id={scan_id}" if scan_id else "/discovery/import",
    }


async def is_setup_complete() -> bool:
    return (await get_app_setting("setup_complete", "0")) == "1"


async def mark_setup_complete() -> None:
    await set_app_setting("setup_complete", "1")


# ─── Topology ─────────────────────────────────────────────


async def set_device_parent(device_id: int, parent_device_id: int | None) -> Optional[dict]:
    if parent_device_id == device_id:
        raise ValueError("Device cannot be its own parent")
    return await update_host(device_id, parent_device_id=parent_device_id)


async def get_topology() -> dict[str, Any]:
    """Logical topology grouped by subnet, location, and device type."""
    hosts = await list_hosts()
    by_subnet: dict[str, list] = {}
    by_location: dict[str, list] = {}
    by_type: dict[str, list] = {}
    for h in hosts:
        seg = h.get("network_segment") or _infer_segment(h.get("address", ""))
        loc = h.get("location") or "Unassigned"
        dtype = h.get("host_type") or "unknown"
        by_subnet.setdefault(seg, []).append(h)
        by_location.setdefault(loc, []).append(h)
        by_type.setdefault(dtype, []).append(h)
    return {
        "devices": hosts,
        "by_subnet": by_subnet,
        "by_location": by_location,
        "by_type": by_type,
        "gateways": [h for h in hosts if (h.get("host_type") or "") in ("router", "gateway")],
    }


def _infer_segment(address: str) -> str:
    parts = str(address).split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
    return "unknown"


async def bulk_set_hosts_enabled(host_ids: list[int], enabled: bool) -> int:
    if not host_ids:
        return 0
    db_conn = await get_db()
    for hid in host_ids:
        await db_conn.execute(
            "UPDATE hosts SET enabled = ?, updated_at = ? WHERE id = ?",
            (int(enabled), _now(), hid),
        )
    placeholders = ",".join("?" * len(host_ids))
    await db_conn.execute(
        f"UPDATE services SET enabled = ?, updated_at = ? WHERE host_id IN ({placeholders})",
        [int(enabled), _now(), *host_ids],
    )
    await db_conn.commit()
    return len(host_ids)


async def bulk_assign_location(host_ids: list[int], location: str) -> int:
    db_conn = await get_db()
    for hid in host_ids:
        await db_conn.execute(
            "UPDATE hosts SET location = ?, updated_at = ? WHERE id = ?",
            (location, _now(), hid),
        )
    await db_conn.commit()
    return len(host_ids)


async def bulk_assign_tags(host_ids: list[int], tags: str) -> int:
    db_conn = await get_db()
    for hid in host_ids:
        await db_conn.execute(
            "UPDATE hosts SET tags = ?, updated_at = ? WHERE id = ?",
            (tags, _now(), hid),
        )
    await db_conn.commit()
    return len(host_ids)


# ─── Maintenance tasks ────────────────────────────────────


async def create_maintenance_task(
    *,
    title: str,
    device_id: int | None = None,
    alert_id: int | None = None,
    priority: str = "medium",
    recommendation: str = "",
) -> dict:
    db_conn = await get_db()
    cursor = await db_conn.execute(
        """INSERT INTO maintenance_tasks
           (title, device_id, alert_id, priority, status, recommendation, created_at)
           VALUES (?, ?, ?, ?, 'open', ?, ?)""",
        (title, device_id, alert_id, priority, recommendation, _now()),
    )
    await db_conn.commit()
    rows = await db_conn.execute_fetchall(
        "SELECT * FROM maintenance_tasks WHERE id = ?", (cursor.lastrowid,)
    )
    return _row_to_dict(rows[0])


async def list_maintenance_tasks(status: str | None = None, limit: int = 100) -> list[dict]:
    db_conn = await get_db()
    if status:
        rows = await db_conn.execute_fetchall(
            "SELECT * FROM maintenance_tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        )
    else:
        rows = await db_conn.execute_fetchall(
            "SELECT * FROM maintenance_tasks ORDER BY created_at DESC LIMIT ?", (limit,)
        )
    return [_row_to_dict(r) for r in rows]


async def get_maintenance_task(task_id: int) -> Optional[dict]:
    db_conn = await get_db()
    rows = await db_conn.execute_fetchall(
        "SELECT * FROM maintenance_tasks WHERE id = ?", (task_id,)
    )
    return _row_to_dict(rows[0]) if rows else None


async def resolve_maintenance_task(task_id: int) -> Optional[dict]:
    db_conn = await get_db()
    await db_conn.execute(
        "UPDATE maintenance_tasks SET status = 'resolved', resolved_at = ? WHERE id = ?",
        (_now(), task_id),
    )
    await db_conn.commit()
    return await get_maintenance_task(task_id)


async def get_enhanced_dashboard() -> dict[str, Any]:
    """Remote-first dashboard sections beyond basic stats."""
    stats = await get_dashboard_stats()
    hosts_status = await get_hosts_status()
    critical = [h for h in hosts_status if h["overall_state"] == "critical"]
    offline = [h for h in hosts_status if h["overall_state"] in ("critical", "warning")]
    recovered = await get_recent_alerts(limit=20)
    recovered = [a for a in recovered if a.get("severity") == "recovery"]
    discovered = await list_discovered_devices(limit=20)
    discovery_needs_monitored_network = False
    last_discovery_refresh = None
    if not discovered:
        for scan in await list_discovery_scans(limit=5):
            if int(scan.get("found_count") or 0) <= 0:
                continue
            scan_devices = await list_discovered_devices(scan_id=int(scan["id"]), limit=20)
            if scan_devices:
                discovered = scan_devices
                discovery_needs_monitored_network = True
                last_discovery_refresh = scan.get("finished_at") or scan.get("created_at")
                break
    new_discovered = [d for d in discovered if not d.get("imported_host_id")]
    open_tasks = await list_maintenance_tasks(status="open", limit=10)
    return {
        **stats,
        "critical_problems": critical[:10],
        "devices_offline": offline[:10],
        "services_failing": stats.get("services_critical", 0) + stats.get("services_warning", 0),
        "recently_recovered": recovered[:5],
        "new_discovered": new_discovered[:10],
        "discovery_needs_monitored_network": discovery_needs_monitored_network,
        "last_discovery_refresh": last_discovery_refresh,
        "open_maintenance_tasks": open_tasks,
        "setup_complete": await is_setup_complete(),
    }


# ─── Notifications ────────────────────────────────────────


async def create_notification(
    *,
    level: str,
    category: str,
    title: str,
    message: str,
    action_url: str = "",
    metadata_json: str = "{}",
    dedupe_key: str = "",
) -> dict:
    db_conn = await get_db()
    now = _now()
    if dedupe_key:
        existing = await db_conn.execute_fetchall(
            """SELECT id FROM notifications
               WHERE dedupe_key = ? AND dismissed_at IS NULL
               ORDER BY id DESC LIMIT 1""",
            (dedupe_key,),
        )
        if existing:
            nid = int(existing[0][0])
            await db_conn.execute(
                """UPDATE notifications SET created_at = ?, level = ?, message = ?, action_url = ?
                   WHERE id = ?""",
                (now, level, message, action_url, nid),
            )
            await db_conn.commit()
            rows = await db_conn.execute_fetchall("SELECT * FROM notifications WHERE id = ?", (nid,))
            return _row_to_dict(rows[0])

    cursor = await db_conn.execute(
        """INSERT INTO notifications
           (created_at, level, category, title, message, action_url, metadata_json, dedupe_key)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (now, level, category, title, message, action_url, metadata_json, dedupe_key),
    )
    await db_conn.commit()
    rows = await db_conn.execute_fetchall(
        "SELECT * FROM notifications WHERE id = ?", (cursor.lastrowid,)
    )
    return _row_to_dict(rows[0])


async def list_notifications(
    *,
    include_dismissed: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    db_conn = await get_db()
    query = "SELECT * FROM notifications"
    params: list[Any] = []
    if not include_dismissed:
        query += " WHERE dismissed_at IS NULL"
    query += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = await db_conn.execute_fetchall(query, params)
    return [_row_to_dict(r) for r in rows]


async def count_unread_notifications() -> int:
    db_conn = await get_db()
    rows = await db_conn.execute_fetchall(
        "SELECT COUNT(*) AS cnt FROM notifications WHERE read_at IS NULL AND dismissed_at IS NULL"
    )
    return int(rows[0]["cnt"] if rows else 0)


async def mark_notification_read(notification_id: int) -> Optional[dict]:
    db_conn = await get_db()
    await db_conn.execute(
        "UPDATE notifications SET read_at = ? WHERE id = ? AND read_at IS NULL",
        (_now(), notification_id),
    )
    await db_conn.commit()
    rows = await db_conn.execute_fetchall("SELECT * FROM notifications WHERE id = ?", (notification_id,))
    return _row_to_dict(rows[0]) if rows else None


async def mark_all_notifications_read() -> int:
    db_conn = await get_db()
    cursor = await db_conn.execute(
        "UPDATE notifications SET read_at = ? WHERE read_at IS NULL AND dismissed_at IS NULL",
        (_now(),),
    )
    await db_conn.commit()
    return int(cursor.rowcount or 0)


async def dismiss_notification(notification_id: int) -> Optional[dict]:
    db_conn = await get_db()
    now = _now()
    await db_conn.execute(
        "UPDATE notifications SET dismissed_at = ?, read_at = COALESCE(read_at, ?) WHERE id = ?",
        (now, now, notification_id),
    )
    await db_conn.commit()
    rows = await db_conn.execute_fetchall("SELECT * FROM notifications WHERE id = ?", (notification_id,))
    return _row_to_dict(rows[0]) if rows else None


async def count_system_logs_filtered(
    *,
    category: Optional[str] = None,
    level: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    errors_only: bool = False,
) -> int:
    db_conn = await get_db()
    conditions: list[str] = []
    params: list[Any] = []
    if category:
        conditions.append("category = ?")
        params.append(category)
    if level:
        conditions.append("level = ?")
        params.append(level)
    if errors_only:
        conditions.append("level IN ('error', 'critical')")
    if search:
        like = f"%{search}%"
        conditions.append("(message LIKE ? OR event_type LIKE ? OR source LIKE ?)")
        params.extend([like, like, like])
    if date_from:
        conditions.append("created_at >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("created_at <= ?")
        params.append(date_to)
    query = "SELECT COUNT(*) AS cnt FROM system_logs"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    rows = await db_conn.execute_fetchall(query, params)
    return int(rows[0]["cnt"] if rows else 0)


async def list_all_app_settings() -> dict[str, str]:
    db_conn = await get_db()
    rows = await db_conn.execute_fetchall("SELECT key, value FROM app_settings ORDER BY key")
    return {str(r["key"]): str(r["value"]) for r in rows}
