"""Authentication API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ditaknet import database as db
from ditaknet.security import (
    AuthenticatedUser,
    authenticate_user,
    create_access_token,
    get_current_user,
)

router = APIRouter(prefix="/auth", tags=["auth"])


async def _credentials_from_request(request: Request) -> tuple[str, str]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        payload = await request.json()
        return str(payload.get("username") or ""), str(payload.get("password") or "")

    form = await request.form()
    return str(form.get("username") or ""), str(form.get("password") or "")


@router.post("/login")
async def login(request: Request) -> dict:
    username, password = await _credentials_from_request(request)
    # Use the same auth resolver as the web dashboard so first-run setup admins
    # and env fallback admins behave consistently.
    user = await authenticate_user(username, password)
    if not user:
        try:
            await db.create_audit_log(
                "api.login.failure",
                actor=username or "unknown",
                resource="auth",
                ip_address=request.client.host if request.client else "",
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(
        user.username,
        role=user.role,
        extra_claims={
            "uid": user.id,
            "is_superadmin": user.is_superadmin,
            "session_version": user.session_version,
        },
    )
    try:
        await db.create_audit_log(
            "api.login.success",
            actor=user.username,
            resource="auth",
            ip_address=request.client.host if request.client else "",
        )
    except Exception:
        pass

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user.to_public_dict(),
    }


@router.get("/me")
async def me(user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    return user.to_public_dict()
