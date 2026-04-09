"""Shared email sending helper.

Auto-detects provider from settings: prefers explicit MAIL_TYPE, falls back to
RESEND_API_KEY → "resend" or SMTP_HOST → "smtp". Returns False if no provider
is configured (callers may decide to log the message instead of failing).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def is_mail_configured() -> bool:
    """Quick check used by /feedback/status etc."""
    from app.config import settings
    if settings.MAIL_TYPE.strip():
        return True
    return bool(settings.RESEND_API_KEY or settings.SMTP_HOST)


async def send_email(to: str, subject: str, body: str) -> bool:
    """Send a plain-text email. Returns True on success, False if no provider
    is configured. Raises on actual delivery errors so callers can decide
    whether to surface them.
    """
    from app.config import settings

    mail_type = settings.MAIL_TYPE.lower().strip()
    if not mail_type:
        if settings.RESEND_API_KEY:
            mail_type = "resend"
        elif settings.SMTP_HOST:
            mail_type = "smtp"
        else:
            return False

    if mail_type == "resend":
        await _send_via_resend(to, subject, body, settings)
    elif mail_type == "smtp":
        await _send_via_smtp(to, subject, body, settings)
    else:
        logger.error("Unknown MAIL_TYPE: %s", mail_type)
        return False
    return True


async def _send_via_resend(to: str, subject: str, body: str, settings) -> None:
    import resend

    resend.api_key = settings.RESEND_API_KEY
    resend.Emails.send({
        "from": settings.MAIL_FROM,
        "to": [to],
        "subject": subject,
        "text": body,
    })
    logger.info("Email sent via Resend to %s (subject=%r)", to, subject)


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
    logger.info("Email sent via SMTP to %s (subject=%r)", to, subject)
