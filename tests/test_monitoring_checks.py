"""Isolated unit tests for monitoring checks and scheduler behavior."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from ditaknet.core.checks import http as http_checks
from ditaknet.core.checks import ping as ping_checks
from ditaknet.core.checks import tcp as tcp_checks
from ditaknet.core.checks.base import BaseCheck, CheckResponse
from ditaknet.core.checks.http import HttpCheck
from ditaknet.core.checks.ping import PingCheck
from ditaknet.core.checks.tcp import TcpCheck
from ditaknet.core.scheduler import CHECK_REGISTRY, Scheduler
from ditaknet.models import ServiceState


class StubProcess:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
        error: BaseException | None = None,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.error = error
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self.error is not None:
            raise self.error
        return self.stdout, self.stderr

    def kill(self) -> None:
        self.killed = True


class StubWriter:
    def __init__(self) -> None:
        self.closed = False
        self.waited_closed = False

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        self.waited_closed = True


class StubHttpClient:
    def __init__(
        self,
        *,
        response: object | None = None,
        error: BaseException | None = None,
        init_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.init_kwargs = init_kwargs or {}
        self.requests: list[tuple[str, str, dict[str, str]]] = []

    async def __aenter__(self) -> StubHttpClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
    ) -> object:
        self.requests.append((method, url, headers))
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


class SequenceCheck(BaseCheck):
    check_type = "sequence"

    def __init__(self, outcomes: list[CheckResponse | BaseException]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    async def execute(
        self,
        target: str,
        *,
        port: int | None = None,
        timeout: int = 10,
        **kwargs: Any,
    ) -> CheckResponse:
        self.calls.append(
            {"target": target, "port": port, "timeout": timeout, **kwargs}
        )
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class StubStateEngine:
    def __init__(self, current: ServiceState = ServiceState.UNKNOWN) -> None:
        self.current = current
        self.removed: list[int] = []

    def get_state(self, _service_id: int) -> ServiceState:
        return self.current

    def remove_service(self, service_id: int) -> None:
        self.removed.append(service_id)


def _scheduler(
    *,
    max_check_attempts: int = 3,
    retry_backoff_seconds: float = 0,
    current_state: ServiceState = ServiceState.UNKNOWN,
) -> Scheduler:
    scheduler = Scheduler.__new__(Scheduler)
    scheduler.state_engine = StubStateEngine(current_state)
    scheduler.alert_engine = SimpleNamespace()
    scheduler.max_check_attempts = max_check_attempts
    scheduler.retry_backoff_seconds = retry_backoff_seconds
    return scheduler


def _service(**overrides: Any) -> dict[str, Any]:
    service: dict[str, Any] = {
        "id": 7,
        "name": "Test service",
        "check_type": "http",
        "target": "monitoring.internal",
        "port": 8080,
        "timeout_seconds": 4,
        "interval_seconds": 60,
    }
    service.update(overrides)
    return service


def _install_http_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status_code: int | None = None,
    history_count: int = 0,
    error: BaseException | None = None,
) -> StubHttpClient:
    response = (
        SimpleNamespace(
            status_code=status_code,
            history=[object()] * history_count,
        )
        if status_code is not None
        else None
    )
    instances: list[StubHttpClient] = []

    def factory(**kwargs: Any) -> StubHttpClient:
        client = StubHttpClient(
            response=response,
            error=error,
            init_kwargs=kwargs,
        )
        instances.append(client)
        return client

    monkeypatch.setattr(http_checks.httpx, "AsyncClient", factory)
    client_placeholder = StubHttpClient()
    client_placeholder.instances = instances  # type: ignore[attr-defined]
    return client_placeholder


def test_builtin_check_registry_has_expected_implementations() -> None:
    assert CHECK_REGISTRY["ping"] is PingCheck
    assert CHECK_REGISTRY["tcp"] is TcpCheck
    assert CHECK_REGISTRY["http"] is HttpCheck


def test_ping_success_uses_parsed_latency_without_real_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = StubProcess(
        stdout=b"64 bytes from 192.0.2.10: time=12.4 ms\n",
    )
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    async def create_subprocess(*args: object, **kwargs: object) -> StubProcess:
        calls.append((args, kwargs))
        return process

    monkeypatch.setattr(ping_checks.asyncio, "create_subprocess_exec", create_subprocess)

    result = asyncio.run(PingCheck().execute("192.0.2.10", timeout=2))

    assert result.success is True
    assert result.response_time_ms == pytest.approx(12.4)
    assert result.extra == {"exit_code": 0, "retryable": False}
    assert calls[0][0][0] == "ping"
    assert calls[0][0][-1] == "192.0.2.10"


def test_ping_failure_output_wins_even_with_zero_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = StubProcess(
        returncode=0,
        stdout=b"1 packets transmitted, 0 received, 100% packet loss\n",
    )

    async def create_subprocess(*_args: object, **_kwargs: object) -> StubProcess:
        return process

    monkeypatch.setattr(ping_checks.asyncio, "create_subprocess_exec", create_subprocess)

    result = asyncio.run(PingCheck().execute("192.0.2.11"))

    assert result.success is False
    assert "100% packet loss" in result.message
    assert result.extra["retryable"] is True


def test_ping_timeout_returns_failure_and_kills_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = StubProcess(error=asyncio.TimeoutError())

    async def create_subprocess(*_args: object, **_kwargs: object) -> StubProcess:
        return process

    monkeypatch.setattr(ping_checks.asyncio, "create_subprocess_exec", create_subprocess)

    result = asyncio.run(PingCheck().execute("192.0.2.12", timeout=0))

    assert result.success is False
    assert result.extra == {"error_type": "timeout", "retryable": True}
    assert "timed out after 0.1s" in result.message
    assert process.killed is True


def test_ping_missing_binary_is_non_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def missing_binary(*_args: object, **_kwargs: object) -> StubProcess:
        raise FileNotFoundError("ping")

    monkeypatch.setattr(ping_checks.asyncio, "create_subprocess_exec", missing_binary)

    result = asyncio.run(PingCheck().execute("192.0.2.13"))

    assert result.success is False
    assert result.message == "Ping command not found"
    assert result.extra == {"error_type": "missing_ping", "retryable": False}


def test_tcp_requires_port_without_opening_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_network(*_args: object, **_kwargs: object) -> None:
        pytest.fail("TCP check attempted network access without a port")

    monkeypatch.setattr(tcp_checks.asyncio, "open_connection", unexpected_network)

    result = asyncio.run(TcpCheck().execute("192.0.2.20", port=None))

    assert result.success is False
    assert result.extra == {"error_type": "missing_port", "retryable": False}


def test_tcp_success_closes_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = StubWriter()
    calls: list[tuple[str, int]] = []

    async def open_connection(target: str, port: int) -> tuple[object, StubWriter]:
        calls.append((target, port))
        return object(), writer

    monkeypatch.setattr(tcp_checks.asyncio, "open_connection", open_connection)

    result = asyncio.run(TcpCheck().execute("192.0.2.21", port=443))

    assert result.success is True
    assert result.extra["retryable"] is False
    assert calls == [("192.0.2.21", 443)]
    assert writer.closed is True
    assert writer.waited_closed is True


def test_tcp_connection_refused_is_definitive_and_non_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refused(*_args: object, **_kwargs: object) -> None:
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(tcp_checks.asyncio, "open_connection", refused)

    result = asyncio.run(TcpCheck().execute("192.0.2.22", port=22))

    assert result.success is False
    assert result.extra == {
        "error_type": "connection_refused",
        "retryable": False,
    }


def test_tcp_timeout_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def timed_out(*_args: object, **_kwargs: object) -> None:
        raise TimeoutError()

    monkeypatch.setattr(tcp_checks.asyncio, "open_connection", timed_out)

    result = asyncio.run(TcpCheck().execute("192.0.2.23", port=25, timeout=0))

    assert result.success is False
    assert result.extra == {"error_type": "timeout", "retryable": True}
    assert "timed out after 0.1s" in result.message


@pytest.mark.parametrize(
    ("target", "port", "expected"),
    [
        ("example.test/path", None, "http://example.test/path"),
        ("https://example.test/path", 8443, "https://example.test:8443/path"),
        ("http://example.test:8080/path", 9000, "http://example.test:8080/path"),
        ("http://[2001:db8::1]/health", 8080, "http://[2001:db8::1]:8080/health"),
    ],
)
def test_http_url_building(target: str, port: int | None, expected: str) -> None:
    assert HttpCheck._build_url(target, port) == expected


def test_http_expected_status_success_uses_mock_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = _install_http_client(monkeypatch, status_code=204)

    result = asyncio.run(
        HttpCheck().execute(
            "service.test/health",
            port=8080,
            timeout=3,
            expected_status_code=204,
            method="HEAD",
            headers={"X-Monitor": "yes"},
        )
    )
    client = installed.instances[0]  # type: ignore[attr-defined]

    assert result.success is True
    assert result.extra["status_code"] == 204
    assert result.extra["status_class"] == "success"
    assert result.extra["retryable"] is False
    assert client.requests == [
        ("HEAD", "http://service.test:8080/health", {"X-Monitor": "yes"})
    ]
    assert client.init_kwargs["follow_redirects"] is True
    assert client.init_kwargs["verify"] is False


def test_http_server_error_is_failed_and_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_http_client(monkeypatch, status_code=503)

    result = asyncio.run(HttpCheck().execute("service.test", expected_status_code=200))

    assert result.success is False
    assert result.extra["status_class"] == "server_error"
    assert result.extra["retryable"] is True
    assert "expected 200" in result.message


def test_http_redirect_is_success_when_no_exact_status_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_http_client(monkeypatch, status_code=302, history_count=2)

    result = asyncio.run(HttpCheck().execute("service.test", expected_status_code=None))

    assert result.success is True
    assert result.extra["status_class"] == "redirect"
    assert result.extra["redirect_count"] == 2


@pytest.mark.parametrize(
    ("error", "error_type", "retryable"),
    [
        (httpx.TimeoutException("slow response"), "timeout", True),
        (httpx.ConnectError("no route"), "connect_error", True),
        (httpx.InvalidURL("bad target"), "invalid_url", False),
    ],
)
def test_http_transport_errors_are_classified_without_network(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    error_type: str,
    retryable: bool,
) -> None:
    _install_http_client(monkeypatch, error=error)

    result = asyncio.run(HttpCheck().execute("service.test", timeout=2))

    assert result.success is False
    assert result.extra["error_type"] == error_type
    assert result.extra["retryable"] is retryable


def test_scheduler_job_registration_replaces_duplicate_id() -> None:
    class StubJobScheduler:
        def __init__(self) -> None:
            self.existing = {"check_service_7"}
            self.removed: list[str] = []
            self.added: list[dict[str, Any]] = []

        def get_job(self, job_id: str) -> object | None:
            return object() if job_id in self.existing else None

        def remove_job(self, job_id: str) -> None:
            self.removed.append(job_id)
            self.existing.discard(job_id)

        def add_job(self, _callable: object, **kwargs: Any) -> None:
            self.added.append(kwargs)

    scheduler = _scheduler()
    backing = StubJobScheduler()
    scheduler._scheduler = backing  # type: ignore[assignment]
    service = _service()

    scheduler._add_job(service)

    assert Scheduler._job_id(7) == "check_service_7"
    assert backing.removed == ["check_service_7"]
    assert backing.added[0]["id"] == "check_service_7"
    assert backing.added[0]["args"] == [service]
    assert backing.added[0]["seconds"] == 60


def test_scheduler_execute_once_passes_service_configuration() -> None:
    checker = SequenceCheck([CheckResponse(success=True, message="ok")])
    scheduler = _scheduler()
    service = _service(expected_status_code=204)
    extra = scheduler._extra_check_kwargs("http", service)

    result = asyncio.run(scheduler._execute_once(checker, service, extra))

    assert result.success is True
    assert checker.calls == [
        {
            "target": "monitoring.internal",
            "port": 8080,
            "timeout": 4,
            "expected_status_code": 204,
        }
    ]


def test_scheduler_execute_once_contains_plugin_exception() -> None:
    checker = SequenceCheck([RuntimeError("plugin exploded")])
    scheduler = _scheduler()

    result = asyncio.run(scheduler._execute_once(checker, _service(), {}))

    assert result.success is False
    assert result.extra == {"error_type": "RuntimeError", "retryable": True}
    assert result.message == "http check error: plugin exploded"


def test_scheduler_retries_transient_failure_then_returns_success() -> None:
    checker = SequenceCheck(
        [
            CheckResponse(
                success=False,
                message="temporary timeout",
                extra={"retryable": True},
            ),
            CheckResponse(success=True, message="recovered"),
        ]
    )
    scheduler = _scheduler(max_check_attempts=3)

    result = asyncio.run(scheduler._execute_with_retries(checker, _service(), {}))

    assert len(checker.calls) == 2
    assert result.success is True
    assert result.extra["attempt"] == 2
    assert result.extra["attempts"] == 2
    assert result.extra["retry_exhausted"] is False
    assert result.message == "recovered (after 2 attempts)"


def test_scheduler_does_not_retry_definitive_failure() -> None:
    checker = SequenceCheck(
        [
            CheckResponse(
                success=False,
                message="configuration error",
                extra={"retryable": False},
            )
        ]
    )
    scheduler = _scheduler(max_check_attempts=3)

    result = asyncio.run(scheduler._execute_with_retries(checker, _service(), {}))

    assert len(checker.calls) == 1
    assert result.extra["attempts"] == 1
    assert result.extra["retry_exhausted"] is False


def test_scheduler_marks_retry_exhaustion() -> None:
    checker = SequenceCheck(
        [
            CheckResponse(False, message="timeout", extra={"retryable": True}),
            CheckResponse(False, message="timeout", extra={"retryable": True}),
        ]
    )
    scheduler = _scheduler(max_check_attempts=2)

    result = asyncio.run(scheduler._execute_with_retries(checker, _service(), {}))

    assert len(checker.calls) == 2
    assert result.extra["attempts"] == 2
    assert result.extra["retry_exhausted"] is True
    assert result.message == "timeout (after 2 attempts)"


@pytest.mark.parametrize(
    ("service_values", "expected"),
    [
        ({"max_attempts": 5}, 5),
        ({"max_attempts": 0}, 1),
        ({"retry_count": 2}, 3),
        ({"max_attempts": "invalid"}, 3),
        ({"retry_count": "invalid"}, 3),
        ({}, 3),
    ],
)
def test_scheduler_normalises_attempt_configuration(
    service_values: dict[str, object],
    expected: int,
) -> None:
    assert _scheduler(max_check_attempts=3)._max_attempts_for_service(service_values) == expected


def test_scheduler_result_status_preserves_degraded_state() -> None:
    failed = CheckResponse(success=False)

    critical_scheduler = _scheduler(current_state=ServiceState.CRITICAL)
    unknown_scheduler = _scheduler(current_state=ServiceState.UNKNOWN)

    assert (
        critical_scheduler._result_status(7, failed, None) is ServiceState.CRITICAL
    )
    assert unknown_scheduler._result_status(7, failed, None) is ServiceState.WARNING
    assert (
        unknown_scheduler._result_status(7, CheckResponse(success=True), None)
        is ServiceState.OK
    )
