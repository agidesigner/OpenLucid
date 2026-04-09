from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.libs.password import hash_password, validate_password, verify_password
from app.models.user import User

logger = logging.getLogger(__name__)


async def needs_setup(db: AsyncSession) -> bool:
    result = await db.execute(select(func.count()).select_from(User))
    return result.scalar() == 0


async def create_admin(db: AsyncSession, email: str, password: str) -> User:
    if not validate_password(password):
        raise ValueError("Password must be at least 8 characters and contain both letters and numbers")
    user = User(email=email.lower(), hashed_password=hash_password(password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def authenticate(db: AsyncSession, email: str, password: str) -> User:
    result = await db.execute(select(User).where(User.email == email.lower()))
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.hashed_password):
        raise ValueError("Incorrect email or password")
    if not user.is_active:
        raise ValueError("Account has been deactivated")
    return user


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email.lower()))
    return result.scalar_one_or_none()


async def update_password(db: AsyncSession, user: User, new_password: str) -> None:
    if not validate_password(new_password):
        raise ValueError("Password must be at least 8 characters and contain both letters and numbers")
    user.hashed_password = hash_password(new_password)
    await db.commit()


async def send_reset_email(email: str, reset_url: str) -> None:
    from app.libs.mail import send_email

    subject = "OpenLucid - Password Reset"
    body = f"""Hello,

Please click the link below to reset your password (valid for 15 minutes):

{reset_url}

If you did not request a password reset, please ignore this email.
"""

    sent = await send_email(email, subject, body)
    if not sent:
        logger.warning("Email not configured. Password reset URL: %s", reset_url)
