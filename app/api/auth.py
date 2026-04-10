from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.application import auth_service
from app.config import settings
from app.libs.jwt_utils import create_access_token, create_reset_token, decode_token, _pwh_snapshot
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
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
    return MessageResponse(message="Signed out")


@router.get("/me", response_model=MeResponse)
async def me(request: Request, db: AsyncSession = Depends(get_db)):
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(401, "Not authenticated")
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(401, "User not found")
    return MeResponse(id=str(user.id), email=user.email, is_active=user.is_active)


@router.post("/change-password", response_model=MessageResponse)
async def change_password(body: ChangePasswordRequest, request: Request, db: AsyncSession = Depends(get_db)):
    uid = getattr(request.state, "user_id", None)
    if not uid:
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
        reset_url = f"{settings.APP_URL}/signin.html?reset_token={token}"
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
