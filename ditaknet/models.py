"""
Pydantic API schemas (not SQLAlchemy ORM models).

DB rows are plain dicts from ``database.py``; these types validate HTTP payloads.
Host = monitored machine; Service = one check target on a host; Agent = optional
remote metrics reporter not tied to a service check.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Optional

from pydantic import BaseModel, Field


# ─── Enums ────────────────────────────────────────────────


class CheckType(str, enum.Enum):
    """Supported check types."""
    PING = "ping"
    HTTP = "http"
    TCP = "tcp"


class ServiceState(str, enum.Enum):
    """Finite states for monitored services."""
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class AlertSeverity(str, enum.Enum):
    """Alert severity levels."""
    WARNING = "warning"
    CRITICAL = "critical"
    RECOVERY = "recovery"


# ─── Host ─────────────────────────────────────────────────


class HostCreate(BaseModel):
    """Payload for creating a host."""
    name: str = Field(..., min_length=1, max_length=255, examples=["web-server-01"])
    address: str = Field(..., min_length=1, max_length=255, examples=["192.168.1.10"])
    host_type: str = Field("server", min_length=1, max_length=64)
    location: str = Field("", max_length=255)
    tags: str = Field("", max_length=255)
    enabled: bool = True


class HostUpdate(BaseModel):
    """Payload for updating a host."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    address: Optional[str] = Field(None, min_length=1, max_length=255)
    host_type: Optional[str] = Field(None, min_length=1, max_length=64)
    location: Optional[str] = Field(None, max_length=255)
    tags: Optional[str] = Field(None, max_length=255)
    enabled: Optional[bool] = None


class Host(BaseModel):
    """Host read model."""
    id: int
    name: str
    address: str
    host_type: str = "server"
    location: str = ""
    tags: str = ""
    enabled: bool
    created_at: datetime
    updated_at: Optional[datetime] = None


# ─── Service ──────────────────────────────────────────────


class ServiceCreate(BaseModel):
    """Payload for creating a service."""
    host_id: int
    name: str = Field(..., min_length=1, max_length=255, examples=["HTTP Check"])
    check_type: CheckType = CheckType.HTTP
    target: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        examples=["https://example.com"],
    )
    port: Optional[int] = Field(None, ge=1, le=65535)
    interval_seconds: int = Field(60, ge=10, le=86400)
    timeout_seconds: int = Field(10, ge=1, le=120)
    expected_status_code: Optional[int] = Field(200, ge=100, le=599)
    retry_count: int = Field(0, ge=0, le=10)
    max_attempts: int = Field(3, ge=1, le=10)
    enabled: bool = True


class ServiceUpdate(BaseModel):
    """Payload for updating a service."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    check_type: Optional[CheckType] = None
    target: Optional[str] = Field(None, min_length=1, max_length=2048)
    port: Optional[int] = Field(None, ge=1, le=65535)
    interval_seconds: Optional[int] = Field(None, ge=10, le=86400)
    timeout_seconds: Optional[int] = Field(None, ge=1, le=120)
    expected_status_code: Optional[int] = Field(None, ge=100, le=599)
    retry_count: Optional[int] = Field(None, ge=0, le=10)
    max_attempts: Optional[int] = Field(None, ge=1, le=10)
    enabled: Optional[bool] = None


class Service(BaseModel):
    """Service read model."""
    id: int
    host_id: int
    name: str
    check_type: CheckType
    target: str
    port: Optional[int] = None
    interval_seconds: int
    timeout_seconds: int
    expected_status_code: Optional[int] = 200
    retry_count: int = 0
    max_attempts: int = 3
    enabled: bool
    current_state: ServiceState = ServiceState.UNKNOWN
    created_at: datetime
    updated_at: Optional[datetime] = None


# ─── Check Result ─────────────────────────────────────────


class CheckResult(BaseModel):
    """Result of a single check execution."""
    id: Optional[int] = None
    service_id: int
    status: ServiceState
    response_time_ms: Optional[float] = None
    message: str = ""
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ─── Alert ────────────────────────────────────────────────


class Alert(BaseModel):
    """Alert record."""
    id: int
    service_id: int
    alert_type: str
    message: str
    severity: AlertSeverity
    acknowledged: bool = False
    created_at: datetime
    resolved_at: Optional[datetime] = None


class AlertAcknowledge(BaseModel):
    """Payload for acknowledging an alert."""
    acknowledged: bool = True


# ─── Dashboard ────────────────────────────────────────────


class DashboardSummary(BaseModel):
    """High-level dashboard stats."""
    total_hosts: int = 0
    total_services: int = 0
    services_ok: int = 0
    services_warning: int = 0
    services_critical: int = 0
    services_unknown: int = 0
    recent_alerts: list[Alert] = []


class HostStatus(BaseModel):
    """Per-host status with child services."""
    host: Host
    services: list[Service] = []
    overall_state: ServiceState = ServiceState.UNKNOWN


# ─── State Log ────────────────────────────────────────────


class StateChange(BaseModel):
    """Record of a state transition."""
    id: Optional[int] = None
    service_id: int
    old_state: ServiceState
    new_state: ServiceState
    reason: str = ""
    changed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ─── Agent / Metrics ──────────────────────────────────────


class AgentRegister(BaseModel):
    """Payload for registering a DitakNet agent."""
    name: str = Field(..., min_length=1, max_length=255)
    hostname: str = Field("", max_length=255)
    host_id: Optional[int] = None


class AgentRegisterResponse(BaseModel):
    """Response returned once when an agent is registered."""
    agent_id: int
    name: str
    token: str
    status: str = "pending"


class AgentHeartbeat(BaseModel):
    """Optional heartbeat payload."""
    status: str = "online"


class AgentMetricsSubmit(BaseModel):
    """System metrics submitted by a DitakNet agent."""
    cpu_percent: float = Field(..., ge=0, le=100)
    memory_percent: float = Field(..., ge=0, le=100)
    disk_percent: float = Field(..., ge=0, le=100)


class Agent(BaseModel):
    """Agent read model (token never included)."""
    id: int
    name: str
    hostname: str
    host_id: Optional[int] = None
    status: str
    last_heartbeat_at: Optional[datetime] = None
    last_metrics_at: Optional[datetime] = None
    enabled: bool = True
    registered_at: datetime


class AgentMetric(BaseModel):
    """Stored agent metric sample."""
    id: int
    agent_id: int
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    collected_at: datetime


class AgentAlert(BaseModel):
    """Metric threshold alert for an agent."""
    id: int
    agent_id: int
    alert_type: str
    message: str
    severity: AlertSeverity
    metric_name: str = ""
    metric_value: Optional[float] = None
    threshold: Optional[float] = None
    acknowledged: bool = False
    created_at: datetime
    resolved_at: Optional[datetime] = None
