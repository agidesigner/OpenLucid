"""Guest access — singleton token that turns on the shareable WebUI link.

The design: at most one `guest_access` row exists at a time. Enabling
generates a fresh secret, hashes it, replaces the row, and returns the
secret to the owner. Disabling deletes the row, invalidating any
outstanding cookies. Verification (cookie or portal URL) is a single
hash lookup.
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
    row = GuestAccess(token_hash=_hash(raw_token))
    db.add(row)
    await db.commit()
    return raw_token


async def disable(db: AsyncSession) -> None:
    await db.execute(delete(GuestAccess))
    await db.commit()


async def is_enabled(db: AsyncSession) -> bool:
    row = await db.scalar(select(GuestAccess).limit(1))
    return row is not None


async def verify(db: AsyncSession, raw_token: str) -> bool:
    """Return True iff the given token matches the stored hash."""
    if not raw_token:
        return False
    row = await db.scalar(
        select(GuestAccess).where(GuestAccess.token_hash == _hash(raw_token))
    )
    return row is not None
