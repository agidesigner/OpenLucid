"""Request-scoped context using contextvars.

Provides the current user_id to deep call sites (adapters, composers)
without threading it through every function signature.

Set by the auth middleware in main.py; read by get_effective_prompt().
"""
from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger(__name__)

# Populated by auth_middleware for every /api/* request.
current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)


def _current_user_uuid() -> uuid.UUID | None:
    """Return a DB-safe user UUID, ignoring guest/open-access sentinels."""
    raw = current_user_id.get()
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        return None


async def get_effective_prompt(preset_key: str, default_getter: Any) -> str:
    """Return the user's override for *preset_key*, or the system default.

    ``default_getter`` is either a callable (no-arg) that returns the
    default text, or a plain string.  The callable form avoids importing
    heavy modules when the override exists.

    Uses an independent short-lived DB session so it never interferes
    with the caller's transaction.  Errors are swallowed with a warning
    — the system default is always returned as fallback.
    """
    uid = _current_user_uuid()
    if uid:
        try:
            from sqlalchemy import select
            from app.database import async_session_factory
            from app.models.user_prompt_preset import UserPromptPreset

            async with async_session_factory() as session:
                row = await session.scalar(
                    select(UserPromptPreset.content).where(
                        UserPromptPreset.user_id == uid,
                        UserPromptPreset.preset_key == preset_key,
                    )
                )
            if row is not None:
                return row
        except Exception:
            logger.warning("get_effective_prompt(%s) DB lookup failed, using default", preset_key, exc_info=True)

    return default_getter() if callable(default_getter) else default_getter
