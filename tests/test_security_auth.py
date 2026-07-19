"""Unit tests for authentication, RBAC, and CSRF security helpers."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Request, status

from ditaknet.security import (
    CSRF_SESSION_KEY,
    PBKDF2_SCHEME,
    PASSWORD_SCHEME,
    AuthenticatedUser,
    ensure_csrf_token,
    get_current_user,
    has_permissions,
    hash_password,
    permissions_for_role,
    require_permissions,
    require_role,
    require_web_permissions,
    validate_csrf_token,
    validate_password_strength,
    verify_password,
    verify_web_csrf,
)


def _request(
    *,
    method: str = "GET",
    path: str = "/settings",
    session: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> Request:
    """Build a minimal request with session state, without starting an app."""
    raw_headers = [
        (name.lower().encode("latin-1"), value.encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": raw_headers,
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 443),
            "session": session if session is not None else {},
        }
    )


def test_scrypt_password_hash_round_trip_and_wrong_password_rejection() -> None:
    password = "S3cure!Monitoring"
    hashed = hash_password(password, salt="fixed-test-salt")

    assert hashed.startswith(f"{PASSWORD_SCHEME}$")
    assert hashed != password
    assert verify_password(password, hashed) is True
    assert verify_password("wrong-password", hashed) is False


def test_hash_password_rejects_empty_and_non_string_values() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        hash_password("")
    with pytest.raises(ValueError, match="must not be empty"):
        hash_password(None)  # type: ignore[arg-type]


def test_verify_password_supports_legacy_pbkdf2_hashes() -> None:
    password = "Legacy!Password42"
    salt = "legacy-test-salt"
    iterations = 1_000
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )
    encoded = base64.urlsafe_b64encode(digest).decode("ascii")
    stored = f"{PBKDF2_SCHEME}${iterations}${salt}${encoded}"

    assert verify_password(password, stored) is True
    assert verify_password("not-the-password", stored) is False


@pytest.mark.parametrize(
    "stored",
    [
        f"{PASSWORD_SCHEME}$malformed",
        f"{PBKDF2_SCHEME}$malformed",
        f"{PBKDF2_SCHEME}$not-a-number$salt$digest",
    ],
)
def test_verify_password_rejects_malformed_hashes_without_raising(stored: str) -> None:
    assert verify_password("candidate", stored) is False


def test_verify_password_rejects_non_string_inputs() -> None:
    assert verify_password(None, "stored") is False  # type: ignore[arg-type]
    assert verify_password("candidate", None) is False  # type: ignore[arg-type]


def test_legacy_plaintext_verification_requires_an_exact_match() -> None:
    assert verify_password("legacy-secret", "legacy-secret") is True
    assert verify_password("Legacy-secret", "legacy-secret") is False


@pytest.mark.parametrize("password", ["", "short", "password", "alllowercase"])
def test_weak_passwords_report_strength_errors(password: str) -> None:
    assert validate_password_strength(password)


@pytest.mark.parametrize("password", ["Str0ng!Pass", "a sufficiently long passphrase"])
def test_strong_passwords_pass_strength_validation(password: str) -> None:
    assert validate_password_strength(password) == []


def test_permissions_for_role_returns_a_defensive_copy() -> None:
    granted = permissions_for_role("viewer")
    assert "dashboard.view" in granted

    granted.add("settings.security")

    assert "settings.security" not in permissions_for_role("viewer")


def test_has_permissions_requires_every_permission() -> None:
    assert has_permissions("viewer", ["dashboard.view", "devices.view"]) is True
    assert has_permissions("viewer", ["dashboard.view", "devices.edit"]) is False
    assert has_permissions("viewer", []) is True


def test_has_permissions_honors_explicit_grants_and_superadmin() -> None:
    assert (
        has_permissions(
            "viewer",
            "settings.security",
            explicit_permissions={"settings.security"},
        )
        is True
    )
    assert has_permissions("viewer", "unknown.permission", is_superadmin=True) is True


def test_authenticated_user_combines_role_and_explicit_permissions() -> None:
    user = AuthenticatedUser(
        username="operator-with-extra-access",
        role="operator",
        explicit_permissions=frozenset({"settings.security"}),
    )

    assert "devices.run_check" in user.permissions
    assert "settings.security" in user.permissions


def test_require_permissions_returns_authorized_user() -> None:
    user = AuthenticatedUser(username="operator", role="operator")
    dependency = require_permissions("devices.run_check", "alerts.acknowledge")

    assert asyncio.run(dependency(user)) is user


def test_require_permissions_returns_consistent_forbidden_error() -> None:
    user = AuthenticatedUser(username="viewer", role="viewer")
    dependency = require_permissions("settings.security")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(dependency(user))

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail["error"] == "permission_denied"


def test_require_role_allows_named_role_and_superadmin_override() -> None:
    dependency = require_role("admin")
    admin = AuthenticatedUser(username="admin", role="admin")
    owner = AuthenticatedUser(
        username="owner",
        role="viewer",
        is_superadmin=True,
    )

    assert asyncio.run(dependency(admin)) is admin
    assert asyncio.run(dependency(owner)) is owner


def test_require_role_rejects_unlisted_role() -> None:
    dependency = require_role("admin")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(dependency(AuthenticatedUser(username="viewer", role="viewer")))

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


def test_web_permission_dependency_uses_explicit_session_permissions() -> None:
    request = _request(
        session={
            "user": {
                "username": "limited-admin",
                "role": "viewer",
                "explicit_permissions": ["settings.security"],
            }
        }
    )
    dependency = require_web_permissions("settings.security")

    assert asyncio.run(dependency(request)) == "limited-admin"


def test_web_permission_dependency_redirects_anonymous_session_to_login() -> None:
    dependency = require_web_permissions("dashboard.view")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(dependency(_request()))

    assert exc_info.value.status_code == status.HTTP_303_SEE_OTHER
    assert exc_info.value.headers == {"Location": "/login"}


def test_ensure_csrf_token_generates_and_reuses_session_token() -> None:
    request = _request()

    first = ensure_csrf_token(request)
    second = ensure_csrf_token(request)

    assert first == second
    assert request.session[CSRF_SESSION_KEY] == first
    assert len(first) >= 32


def test_validate_csrf_token_accepts_exact_session_token() -> None:
    request = _request(session={CSRF_SESSION_KEY: "expected-token"})

    validate_csrf_token(request, "expected-token")


@pytest.mark.parametrize("submitted", [None, "", "wrong-token"])
def test_validate_csrf_token_rejects_missing_or_mismatched_token(
    submitted: str | None,
) -> None:
    request = _request(session={CSRF_SESSION_KEY: "expected-token"})

    with pytest.raises(HTTPException) as exc_info:
        validate_csrf_token(request, submitted)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc_info.value.detail == "Invalid or missing CSRF token"


def test_validate_csrf_token_rejects_request_without_session_token() -> None:
    with pytest.raises(HTTPException) as exc_info:
        validate_csrf_token(_request(), "submitted-token")

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


def test_verify_web_csrf_validates_authenticated_html_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(
        method="POST",
        path="/settings",
        session={CSRF_SESSION_KEY: "expected-token"},
        headers={"X-CSRF-Token": "expected-token"},
    )

    asyncio.run(verify_web_csrf(request))


def test_verify_web_csrf_rejects_bad_header_on_html_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _request(
        method="POST",
        path="/settings",
        session={CSRF_SESSION_KEY: "expected-token"},
        headers={"X-CSRF-Token": "wrong-token"},
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(verify_web_csrf(request))

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/settings"), ("POST", "/api/devices")],
)
def test_verify_web_csrf_skips_non_post_and_exempt_routes(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    asyncio.run(verify_web_csrf(_request(method=method, path=path)))


def test_verify_web_csrf_protects_first_run_setup_posts() -> None:
    request = _request(
        method="POST",
        path="/setup/admin",
        session={CSRF_SESSION_KEY: "expected-token"},
        headers={"X-CSRF-Token": "wrong-token"},
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(verify_web_csrf(request))

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


def test_session_authenticated_api_mutation_requires_csrf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ditaknet.security._active_user_from_database",
        AsyncMock(return_value=None),
    )
    request = _request(
        method="POST",
        path="/api/backups/create",
        session={
            CSRF_SESSION_KEY: "expected-token",
            "user": {"username": "admin", "role": "admin"},
        },
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(get_current_user(request, credentials=None))

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


def test_session_authenticated_api_mutation_accepts_csrf_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ditaknet.security._active_user_from_database",
        AsyncMock(return_value=None),
    )
    request = _request(
        method="DELETE",
        path="/api/backups/example.zip",
        session={
            CSRF_SESSION_KEY: "expected-token",
            "user": {"username": "admin", "role": "admin"},
        },
        headers={"X-CSRF-Token": "expected-token"},
    )

    user = asyncio.run(get_current_user(request, credentials=None))

    assert user.username == "admin"
