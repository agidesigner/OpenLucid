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
    from app.config import settings

    mail_type = settings.MAIL_TYPE.lower().strip()

    # Auto-detect: if MAIL_TYPE not set, infer from available config
    if not mail_type:
        if settings.RESEND_API_KEY:
            mail_type = "resend"
        elif settings.SMTP_HOST:
            mail_type = "smtp"
        else:
            logger.warning("Email not configured. Password reset URL: %s", reset_url)
            return

    subject = "OpenLucid - Password Reset"
    body = f"""Hello,

Please click the link below to reset your password (valid for 15 minutes):

{reset_url}

If you did not request a password reset, please ignore this email.
"""

    if mail_type == "resend":
        await _send_via_resend(email, subject, body, settings)
    elif mail_type == "smtp":
        await _send_via_smtp(email, subject, body, settings)
    else:
        logger.error("Unknown MAIL_TYPE: %s", mail_type)


async def _send_via_resend(to: str, subject: str, body: str, settings) -> None:
    import resend

    resend.api_key = settings.RESEND_API_KEY
    resend.Emails.send({
        "from": settings.MAIL_FROM,
        "to": [to],
        "subject": subject,
        "text": body,
    })
    logger.info("Password reset email sent via Resend to %s", to)


async def _send_via_smtp(to: str, subject: str, body: str, settings) -> None:
    import aiosmtplib
    from email.mime.text import MIMEText

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = settings.MAIL_FROM
    msg["To"] = to

    await aiosmtplib.send(
        msg,
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        username=settings.SMTP_USER or None,
        password=settings.SMTP_PASSWORD or None,
        start_tls=True,
    )
    logger.info("Password reset email sent via SMTP to %s", to)
