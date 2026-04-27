from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_owner
from app.application import auth_service, guest_access_service
from app.config import settings
from app.libs.jwt_utils import create_access_token, create_reset_token, decode_token, _pwh_snapshot
from app.libs.url_utils import get_public_base_url
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    GuestAccessResponse,
    GuestAccessStatusResponse,
    MeResponse,
    MessageResponse,
    ResetPasswordRequest,
    SetupRequest,
    SetupStatusResponse,
    SignInRequest,
)

from app.libs.rate_limit import check_rate_limit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE = "od_access_token"
GUEST_COOKIE = "od_guest"


# ── Auth-middleware sentinel strings ───────────────────────────────────
#
# ``request.state.user_id`` is a tagged-union: it's EITHER a real user
# UUID string (for JWT-authenticated requests) OR one of these three
# sentinel strings the middleware sets for non-user code paths. Any
# endpoint that loads a User row with ``WHERE User.id == uid`` MUST
# branch on these BEFORE the SQL query, otherwise asyncpg crashes
# trying to cast e.g. ``"no-auth"`` to UUID — that's what produced the
# 500s on /api/v1/auth/me in production. Centralizing the sentinels
# here (vs scattering magic strings) makes the dispatch rule visible
# and prevents the next consumer from forgetting one.
SENTINEL_API_TOKEN = "api-token"
SENTINEL_GUEST = "guest"
SENTINEL_NO_AUTH = "no-auth"
NON_USER_SENTINELS = frozenset({SENTINEL_API_TOKEN, SENTINEL_GUEST, SENTINEL_NO_AUTH})


def is_real_user_uid(uid: str | None) -> bool:
    """True iff ``uid`` is a candidate for ``User.id == uid`` lookup
    (parses as a UUID). Use as the gate before any SQL query keyed on
    the user-id field.

    UUID-format check (allowlist) is stronger than sentinel-denylist:
    a future middleware path adding a new sentinel string will be
    rejected here automatically, instead of slipping through to
    ``WHERE User.id == "<new-sentinel>"`` and crashing asyncpg with a
    500. The known sentinels are still listed in ``NON_USER_SENTINELS``
    so endpoints can route them to friendly responses (guest/api-token
    short-circuits in /auth/me) BEFORE this guard fires."""
    if not uid or uid in NON_USER_SENTINELS:
        return False
    import uuid as _uuid
    try:
        _uuid.UUID(uid)
    except (ValueError, TypeError):
        return False
    return True


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=settings.JWT_EXPIRE_HOURS * 3600,
        path="/",
        secure=settings.APP_URL.startswith("https"),
    )


@router.get("/setup-status", response_model=SetupStatusResponse)
async def setup_status(db: AsyncSession = Depends(get_db)):
    return SetupStatusResponse(needs_setup=await auth_service.needs_setup(db))


@router.post("/setup", response_model=MeResponse)
async def setup(body: SetupRequest, response: Response, db: AsyncSession = Depends(get_db)):
    if not await auth_service.needs_setup(db):
        raise HTTPException(400, "Setup has already been completed")
    if body.password != body.password_confirm:
        raise HTTPException(400, "Passwords do not match")
    try:
        user = await auth_service.create_admin(db, str(body.email), body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _set_cookie(response, create_access_token(str(user.id), user.email))
    return MeResponse(id=str(user.id), email=user.email, is_active=user.is_active)


@router.post("/signin", response_model=MeResponse)
async def signin(body: SignInRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    check_rate_limit(request)
    try:
        user = await auth_service.authenticate(db, str(body.email), body.password)
    except ValueError as e:
        raise HTTPException(401, str(e))
    _set_cookie(response, create_access_token(str(user.id), user.email))
    return MeResponse(id=str(user.id), email=user.email, is_active=user.is_active)


@router.post("/signout", response_model=MessageResponse)
async def signout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    response.delete_cookie(GUEST_COOKIE, path="/")
    return MessageResponse(message="Signed out")


@router.get("/me", response_model=MeResponse)
async def me(request: Request, db: AsyncSession = Depends(get_db)):
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(401, "Not authenticated")
    if uid == SENTINEL_GUEST:
        return MeResponse(id=None, email=None, is_active=True, is_guest=True)
    if uid == SENTINEL_API_TOKEN:
        return MeResponse(id=None, email=None, is_active=True, is_guest=False)
    if uid == SENTINEL_NO_AUTH:
        # Open-access mode (no MCP tokens minted, no JWT cookie). Treat
        # as NOT authenticated for the purposes of /me — the frontend
        # uses a 401 here to redirect users to /signin.html. Returning
        # a friendly MeResponse would let the WebUI render an
        # owner-style dashboard (avatar, menu, settings link) for a
        # signed-out browser session, which is exactly the bug the
        # operator hit after signout. Open-access is for MCP / API
        # callers, never for the WebUI dashboard. The earlier 500
        # crash is still avoided because we never reach the DB query.
        raise HTTPException(401, "Not authenticated")
    if not is_real_user_uid(uid):
        # Defense in depth: a future middleware path adding a new
        # sentinel without updating this function would otherwise
        # land back in the 500 trap. Treat unknown non-UUID strings
        # as not-authenticated rather than letting them reach the DB.
        raise HTTPException(401, "Not authenticated")
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(401, "User not found")
    return MeResponse(id=str(user.id), email=user.email, is_active=user.is_active)


# ── Guest mode ─────────────────────────────────────────────────────────
#
# Owner-only: toggles the shareable guest link on/off. A single row in
# `guest_access` encodes the currently-valid token (hashed). Disabling
# deletes the row and instantly invalidates any outstanding cookies.


@router.get("/guest", response_model=GuestAccessStatusResponse, dependencies=[Depends(require_owner)])
async def guest_status(request: Request, db: AsyncSession = Depends(get_db)):
    raw_token = await guest_access_service.get_raw_token(db)
    url = None
    if raw_token:
        url = f"{get_public_base_url(request)}/guest-access?t={raw_token}"
    return GuestAccessStatusResponse(enabled=raw_token is not None or await guest_access_service.is_enabled(db), url=url)


@router.post("/guest", response_model=GuestAccessResponse, dependencies=[Depends(require_owner)])
async def enable_guest(request: Request, db: AsyncSession = Depends(get_db)):
    raw_token = await guest_access_service.enable(db)
    base = get_public_base_url(request)
    return GuestAccessResponse(
        enabled=True,
        url=f"{base}/guest-access?t={raw_token}",
    )


@router.delete("/guest", response_model=MessageResponse, dependencies=[Depends(require_owner)])
async def disable_guest(db: AsyncSession = Depends(get_db)):
    await guest_access_service.disable(db)
    return MessageResponse(message="Guest mode disabled")


@router.post("/change-password", response_model=MessageResponse)
async def change_password(body: ChangePasswordRequest, request: Request, db: AsyncSession = Depends(get_db)):
    uid = getattr(request.state, "user_id", None)
    # Reject all middleware sentinels — none of them represent a real
    # User row whose password could be changed. Without this guard,
    # ``api-token`` / ``guest`` / ``no-auth`` all reached
    # ``WHERE User.id == uid`` and crashed with asyncpg "invalid UUID".
    if not is_real_user_uid(uid):
        raise HTTPException(401, "Not authenticated")
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(401, "User not found")
    from app.libs.password import verify_password
    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(400, "Current password is incorrect")
    try:
        await auth_service.update_password(db, user, body.new_password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return MessageResponse(message="Password updated")


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(body: ForgotPasswordRequest, request: Request, db: AsyncSession = Depends(get_db)):
    check_rate_limit(request)
    user = await auth_service.get_user_by_email(db, str(body.email))
    if user:
        token = create_reset_token(user.email, user.hashed_password)
        base = get_public_base_url(request)
        reset_url = f"{base}/signin.html?reset_token={token}"
        await auth_service.send_reset_email(user.email, reset_url)
    # Always return success to avoid email enumeration
    return MessageResponse(message="If the email is registered, a reset link has been sent")


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(body: ResetPasswordRequest, request: Request, db: AsyncSession = Depends(get_db)):
    check_rate_limit(request)
    if body.new_password != body.password_confirm:
        raise HTTPException(400, "Passwords do not match")
    try:
        payload = decode_token(body.token)
    except ValueError:
        raise HTTPException(400, "Reset link is invalid or has expired")
    if payload.get("type") != "reset":
        raise HTTPException(400, "Invalid reset link")

    user = await auth_service.get_user_by_email(db, payload.get("email", ""))
    if not user:
        raise HTTPException(400, "User not found")
    if _pwh_snapshot(user.hashed_password) != payload.get("pwh"):
        raise HTTPException(400, "Reset link has expired (password was already changed)")

    try:
        await auth_service.update_password(db, user, body.new_password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return MessageResponse(message="Password has been reset, please sign in again")
