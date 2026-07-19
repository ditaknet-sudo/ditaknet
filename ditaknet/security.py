"""Authentication, password hashing, JWT, and role helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Iterable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ditaknet.config import settings
from ditaknet.core.rbac import DEFAULT_ROLES, permissions_for_role as _catalog_permissions
from ditaknet.core.rbac import normalize_role

PASSWORD_SCHEME = "scrypt_sha256"
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
PBKDF2_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 260_000
JWT_ALGORITHM = "HS256"
LOGIN_LOCK_THRESHOLD = 5
LOGIN_LOCK_MINUTES = 15


ROLE_PERMISSIONS: dict[str, set[str]] = {
    code: set(role.permissions) for code, role in DEFAULT_ROLES.items()
}

HR_PERMISSIONS: frozenset[str] = frozenset(
    perm
    for perms in ROLE_PERMISSIONS.values()
    for perm in perms
    if perm.startswith("hr.") or perm.startswith("employees.") or perm.startswith("attendance.")
)

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthenticatedUser:
    username: str
    role: str = "viewer"
    id: int | None = None
    is_active: bool = True
    is_superadmin: bool = False
    explicit_permissions: frozenset[str] = field(default_factory=frozenset)
    session_version: int = 0
    must_change_password: bool = False

    @property
    def permissions(self) -> set[str]:
        if self.is_superadmin:
            return set(_catalog_permissions("super_admin")) | {"admin"}
        return set(_catalog_permissions(self.role)) | set(self.explicit_permissions)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "is_active": self.is_active,
            "is_superadmin": self.is_superadmin,
            "must_change_password": self.must_change_password,
            "permissions": sorted(self.permissions),
        }


def hash_password(password: str, *, salt: str | None = None) -> str:
    """Hash a password using scrypt-SHA256.

    Existing PBKDF2 hashes remain valid through ``verify_password``. New hashes
    use scrypt because it is memory-hard and available in Python's stdlib.
    """
    if not isinstance(password, str) or password == "":
        raise ValueError("Password must not be empty")
    salt_value = salt or secrets.token_urlsafe(24)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt_value.encode("utf-8"),
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
    )
    encoded = base64.urlsafe_b64encode(digest).decode("ascii")
    return f"{PASSWORD_SCHEME}${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt_value}${encoded}"


def _verify_scrypt(password: str, stored_password: str) -> bool:
    try:
        _scheme, n, r, p, salt, encoded_digest = stored_password.split("$", 5)
        expected = base64.urlsafe_b64decode(encoded_digest.encode("ascii"))
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt.encode("utf-8"),
            n=int(n),
            r=int(r),
            p=int(p),
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _verify_pbkdf2(password: str, stored_password: str) -> bool:
    try:
        _scheme, iterations, salt, encoded_digest = stored_password.split("$", 3)
        expected = base64.urlsafe_b64decode(encoded_digest.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            int(iterations),
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def verify_password(password: str, stored_password: str) -> bool:
    """Verify a password against scrypt, PBKDF2, or legacy plaintext settings."""
    if not isinstance(password, str) or not isinstance(stored_password, str):
        return False
    if stored_password.startswith(f"{PASSWORD_SCHEME}$"):
        return _verify_scrypt(password, stored_password)
    if stored_password.startswith(f"{PBKDF2_SCHEME}$"):
        return _verify_pbkdf2(password, stored_password)
    return hmac.compare_digest(password, stored_password)


def validate_password_strength(password: str) -> list[str]:
    errors: list[str] = []
    if len(password or "") < 10:
        errors.append("Password must be at least 10 characters.")
    if password.lower() in {"password", "admin", "change-me", "ditaknet"}:
        errors.append("Password is too common.")
    if password and len(password) < 16:
        classes = [
            any(ch.islower() for ch in password),
            any(ch.isupper() for ch in password),
            any(ch.isdigit() for ch in password),
            any(not ch.isalnum() for ch in password),
        ]
        if sum(1 for ok in classes if ok) < 3:
            errors.append("Use at least three of lowercase, uppercase, numbers, and symbols.")
    return errors


def _user_from_db_row(row: dict[str, Any]) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=int(row["id"]) if row.get("id") is not None else None,
        username=str(row.get("username") or ""),
        role=normalize_role(row.get("role")),
        is_active=bool(row.get("is_active")),
        is_superadmin=bool(row.get("is_superadmin")),
        explicit_permissions=frozenset(row.get("explicit_permissions") or []),
        session_version=int(row.get("session_version") or 0),
        must_change_password=bool(row.get("must_change_password")),
    )


def _is_locked(row: dict[str, Any]) -> bool:
    locked_until = row.get("locked_until")
    if not locked_until:
        return False
    try:
        until = datetime.fromisoformat(str(locked_until))
        if until.tzinfo is None:
            until = until.replace(tzinfo=UTC)
        return until > datetime.now(UTC)
    except Exception:
        return False


def authenticate_admin(username: str, password: str) -> AuthenticatedUser | None:
    """Sync auth against env-configured admin (tests and fallback)."""
    configured_password = settings.admin_password.strip()
    if configured_password.lower() in {"", "change-me", "changeme", "admin", "password"}:
        return None
    if not hmac.compare_digest(username, settings.admin_username):
        return None
    if not verify_password(password, configured_password):
        return None
    role = normalize_role(settings.admin_role)
    return AuthenticatedUser(username=username, role=role, is_superadmin=role == "super_admin")


async def authenticate_user(username: str, password: str) -> AuthenticatedUser | None:
    """Authenticate DB users first, then setup/env fallback for upgrades."""
    from ditaknet import database as db

    username = str(username or "").strip()
    if not username or not password:
        return None

    try:
        row = await db.get_user_by_username(username)
    except Exception:
        row = None

    if row:
        if not row.get("is_active") or _is_locked(row):
            return None
        if verify_password(password, str(row.get("password_hash") or "")):
            await db.record_user_login(int(row["id"]))
            refreshed = await db.get_user_by_id(int(row["id"]))
            return _user_from_db_row(refreshed or row)
        await db.record_failed_login(
            username,
            threshold=LOGIN_LOCK_THRESHOLD,
            lock_minutes=LOGIN_LOCK_MINUTES,
        )
        return None

    stored_user = await db.get_app_setting("admin_username")
    stored_hash = await db.get_app_setting("admin_password_hash")
    if stored_user and stored_hash:
        if hmac.compare_digest(username, stored_user) and verify_password(password, stored_hash):
            return AuthenticatedUser(username=username, role="super_admin", is_superadmin=True)

    return authenticate_admin(username, password)


def _jwt_secret() -> str:
    return settings.jwt_secret or settings.effective_secret_key


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def create_access_token(
    subject: str,
    *,
    role: str = "viewer",
    expires_in_seconds: int | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Create a signed compact JWT without external dependencies."""
    now = int(time.time())
    ttl = (
        expires_in_seconds
        if expires_in_seconds is not None
        else max(settings.jwt_expire_minutes, 1) * 60
    )
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    payload = {
        "sub": subject,
        "role": normalize_role(role),
        "iat": now,
        "exp": now + int(ttl),
    }
    if extra_claims:
        payload.update(extra_claims)

    header_part = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_part = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_part}.{payload_part}".encode("ascii")
    signature = hmac.new(_jwt_secret().encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_part}.{payload_part}.{_b64url_encode(signature)}"


def decode_access_token(token: str) -> dict[str, Any]:
    """Validate and decode a JWT."""
    try:
        header_part, payload_part, signature_part = token.split(".", 2)
        signing_input = f"{header_part}.{payload_part}".encode("ascii")
        expected = hmac.new(_jwt_secret().encode("utf-8"), signing_input, hashlib.sha256).digest()
        provided = _b64url_decode(signature_part)
        if not hmac.compare_digest(expected, provided):
            raise ValueError("Invalid token signature")
        header = json.loads(_b64url_decode(header_part))
        if header.get("alg") != JWT_ALGORITHM:
            raise ValueError("Unsupported token algorithm")
        payload = json.loads(_b64url_decode(payload_part))
        if int(payload.get("exp", 0)) < int(time.time()):
            raise ValueError("Token expired")
        if not payload.get("sub"):
            raise ValueError("Token subject missing")
        return payload
    except Exception as exc:
        raise ValueError("Invalid access token") from exc


def permissions_for_role(role: str) -> set[str]:
    return set(_catalog_permissions(role))


def has_permissions(
    role: str,
    permissions: Iterable[str],
    *,
    explicit_permissions: Iterable[str] | None = None,
    is_superadmin: bool = False,
) -> bool:
    if isinstance(permissions, str):
        permissions = (permissions,)
    required = [permission for permission in permissions if permission]
    if not required:
        return True
    if is_superadmin:
        return True
    granted = permissions_for_role(role) | set(explicit_permissions or [])
    return all(permission in granted for permission in required)


def has_hr_permission(role: str, permission: str) -> bool:
    return permission in permissions_for_role(role)


def has_office_permission(role: str, permission: str) -> bool:
    return permission in permissions_for_role(role)


def session_role_from_request(request: Request) -> str:
    user = user_from_session(request)
    if user:
        return user.role
    return normalize_role(str(request.session.get("role") or "viewer"))


def require_web_permissions(*required: str):
    """Session-based RBAC guard for HTML routes; returns the authenticated username."""

    async def dependency(request: Request) -> str:
        payload = _session_user_dict(request)
        username = str((payload or {}).get("username") or "")
        if not username:
            raise HTTPException(status_code=303, headers={"Location": "/login"})
        role = normalize_role(str((payload or {}).get("role") or session_role_from_request(request)))
        explicit = frozenset((payload or {}).get("explicit_permissions") or (payload or {}).get("permissions") or [])
        is_superadmin = bool((payload or {}).get("is_superadmin"))
        if not has_permissions(
            role,
            required,
            explicit_permissions=explicit,
            is_superadmin=is_superadmin,
        ):
            raise _permission_denied()
        return username

    return dependency


def _normalize_session_role(username: str, role: str) -> str:
    if username and settings.admin_username and hmac.compare_digest(username, settings.admin_username):
        return normalize_role(role or settings.admin_role or "admin")
    return normalize_role(role)


def _session_user_dict(request: Request) -> dict[str, Any] | None:
    user = request.session.get("user")
    if not user:
        return None
    if isinstance(user, dict):
        return dict(user)
    return {
        "username": str(user),
        "role": str(request.session.get("role") or settings.admin_role or "admin"),
        "permissions": request.session.get("permissions") or [],
        "is_superadmin": bool(request.session.get("is_superadmin", False)),
        "session_version": int(request.session.get("session_version") or 0),
    }


def user_from_session(request: Request) -> AuthenticatedUser | None:
    data = _session_user_dict(request)
    if not data:
        return None
    username = str(data.get("username") or "")
    if not username:
        return None
    role = _normalize_session_role(username, str(data.get("role") or "viewer"))
    request.session["role"] = role
    return AuthenticatedUser(
        id=int(data["id"]) if data.get("id") is not None else None,
        username=username,
        role=role,
        is_active=bool(data.get("is_active", True)),
        is_superadmin=bool(data.get("is_superadmin", False)),
        explicit_permissions=frozenset(data.get("explicit_permissions") or data.get("permissions") or []),
        session_version=int(data.get("session_version") or 0),
        must_change_password=bool(data.get("must_change_password", False)),
    )


async def _active_user_from_database(session_user: AuthenticatedUser) -> AuthenticatedUser | None:
    from ditaknet import database as db

    row = None
    if session_user.id is not None:
        row = await db.get_user_by_id(session_user.id)
    if row is None:
        row = await db.get_user_by_username(session_user.username)
    if row is None:
        return None
    current = _user_from_db_row(row)
    if not current.is_active or _is_locked(row):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User is disabled or locked",
        )
    if session_user.id is not None and current.session_version != session_user.session_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has been revoked",
        )
    return current


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedUser:
    """Resolve authenticated user from Bearer JWT or signed web session."""
    if credentials and credentials.scheme.lower() == "bearer":
        try:
            payload = decode_access_token(credentials.credentials)
            token_user = AuthenticatedUser(
                id=int(payload["uid"]) if payload.get("uid") is not None else None,
                username=str(payload["sub"]),
                role=normalize_role(str(payload.get("role") or "viewer")),
                is_superadmin=bool(payload.get("is_superadmin", False)),
                session_version=int(payload.get("session_version") or 0),
            )
            current = await _active_user_from_database(token_user)
            return current or token_user
        except HTTPException:
            raise
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    session_user = user_from_session(request)
    if session_user:
        if request.url.path.startswith("/api/") and request.method.upper() in {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        }:
            validate_csrf_token(request, request.headers.get("X-CSRF-Token"))
        current = await _active_user_from_database(session_user)
        return current or session_user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _permission_denied() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error": "permission_denied",
            "message": "You do not have permission to access this action.",
        },
    )


def require_permissions(*required: str):
    async def dependency(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        if not has_permissions(
            user.role,
            required,
            explicit_permissions=user.explicit_permissions,
            is_superadmin=user.is_superadmin,
        ):
            raise _permission_denied()
        return user

    return dependency


require_permission = require_permissions


def require_hr_permissions(*required: str):
    async def dependency(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        if not has_permissions(
            user.role,
            required,
            explicit_permissions=user.explicit_permissions,
            is_superadmin=user.is_superadmin,
        ):
            raise _permission_denied()
        return user

    return dependency


FEATURE_ALIASES = {
    "employee_attendance_enabled": "employee_presence_enabled",
    "multi_office_enabled": "multi_office_enabled",
    "branch_agent_enabled": "branch_agent_enabled",
}


def require_feature(feature: str):
    async def dependency(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        from ditaknet.core.licensing import license_service

        status_payload = await license_service.status()
        key = FEATURE_ALIASES.get(feature, feature)
        if not bool(status_payload.get(key)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "feature_disabled",
                    "message": "Your license does not include this module.",
                },
            )
        return user

    return dependency


def require_role(*roles: str):
    allowed = {normalize_role(role) for role in roles}

    async def dependency(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        if user.role not in allowed and not user.is_superadmin:
            raise _permission_denied()
        return user

    return dependency


CSRF_SESSION_KEY = "csrf_token"


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


def validate_csrf_token(request: Request, submitted: str | None) -> None:
    expected = request.session.get(CSRF_SESSION_KEY)
    if not expected or not submitted or not hmac.compare_digest(str(expected), str(submitted)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing CSRF token",
        )


async def verify_web_csrf(request: Request) -> None:
    """Validate CSRF token on authenticated HTML form POST requests."""
    if request.method != "POST":
        return
    path = request.url.path
    if path.startswith("/api/"):
        return
    token = request.headers.get("X-CSRF-Token")
    if not token:
        try:
            form = await request.form()
            token = str(form.get("csrf_token") or form.get("login_csrf") or "")
        except Exception:
            token = ""
    validate_csrf_token(request, token)
