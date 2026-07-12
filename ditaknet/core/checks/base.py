"""
Base check interface and ``CheckResponse`` result shape.

To add a check type:
  1. Subclass ``BaseCheck`` and set ``check_type``
  2. Implement ``execute()`` — honour ``timeout``; return ``CheckResponse``
  3. Register in ``scheduler.CHECK_REGISTRY`` or via a plugin at startup
  4. Add enum/value to API models if exposed in forms

``success=False`` drives the state engine; ``message`` is stored on check_results.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Optional


@dataclass
class CheckResponse:
    """Standardised result from any check execution."""

    success: bool
    response_time_ms: float = 0.0
    message: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    extra: dict = field(default_factory=dict)


class BaseCheck(ABC):
    """Abstract base class for all monitoring checks.

    Subclasses must implement ``execute`` which performs the actual
    connectivity/health test and returns a ``CheckResponse``.
    """

    #: Human-readable name used in logs and alerts
    check_type: str = "base"

    @abstractmethod
    async def execute(
        self,
        target: str,
        *,
        port: Optional[int] = None,
        timeout: int = 10,
        **kwargs,
    ) -> CheckResponse:
        """Run the check against *target* and return a ``CheckResponse``.

        Parameters
        ----------
        target:
            Hostname, IP address, or URL depending on check type.
        port:
            Optional TCP port (used by TCP/HTTP checks).
        timeout:
            Maximum seconds to wait before declaring failure.
        """
        ...
