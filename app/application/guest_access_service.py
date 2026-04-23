"""Guest access — singleton token that turns on the shareable WebUI link.

The design: at most one `guest_access` row exists at a time. Enabling
generates a fresh secret, stores both its SHA-256 hash (for fast verify)
and the raw value (so the owner can view and re-copy the URL anytime).
Regenerating replaces the row and instantly invalidates any outstanding
cookies.
"""
from __future__ import annotations

import hashlib
import secrets

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.guest_access import GuestAccess


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def enable(db: AsyncSession) -> str:
    """Generate a fresh token, replace any existing row, return the raw token."""
    await db.execute(delete(GuestAccess))
    raw_token = secrets.token_urlsafe(32)
    row = GuestAccess(token_hash=_hash(raw_token), raw_token=raw_token)
    db.add(row)
    await db.commit()
    return raw_token


async def disable(db: AsyncSession) -> None:
    await db.execute(delete(GuestAccess))
    await db.commit()


async def is_enabled(db: AsyncSession) -> bool:
    row = await db.scalar(select(GuestAccess).limit(1))
    return row is not None


async def get_raw_token(db: AsyncSession) -> str | None:
    """Return the currently-active share token, or None if guest mode
    is disabled — or if the row pre-dates the raw_token column (upgrade
    from < v0.9.9.8) and the owner hasn't regenerated since.

    Used by the Settings UI so the owner can view + copy the share URL
    anytime, not just the moment they click "enable".
    """
    row = await db.scalar(select(GuestAccess).limit(1))
    if row is None:
        return None
    return row.raw_token


async def verify(db: AsyncSession, raw_token: str) -> bool:
    """Return True iff the given token matches the stored hash."""
    if not raw_token:
        return False
    row = await db.scalar(
        select(GuestAccess).where(GuestAccess.token_hash == _hash(raw_token))
    )
    return row is not None
