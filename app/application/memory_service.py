"""Memory service — CRUD + scope-aware retrieval + prompt rendering.

The service is intentionally flat (free functions, not a class) to
match the rest of `app/application/` — most services here either expose
a class OR free functions; memory has no per-call state to encapsulate
so functions read cleaner.

Three load-bearing pieces:

1. **Scope merge** (``list_memories_for_offer``). Every assembler that
   wants the user's prefs for image or script generation on a given
   offer asks one question: "what does the user want for this offer in
   this merchant?" The answer is always offer-scoped + merchant-scoped
   memories combined, ordered most-specific-first. Surface is filtered
   to the current generator's surface plus 'all'.

2. **Cap enforcement** (``add_memory``). v1 hard cap = 50 active
   entries per scope. The cap exists to bound prompt size — a runaway
   list (100s of nearly-identical preferences) would bloat every
   generation prompt and slowly degrade quality. Reaching the cap is
   a clear signal the user should curate; we surface a 4xx with a
   clear message rather than auto-evicting old entries (would silently
   forget important rules).

3. **Prompt rendering** (``render_memories_block``). The shape of the
   suffix block is deliberately strict ("must strictly follow",
   numbered list, language-matched header). It's appended verbatim by
   each assembler — see `app/application/image_service.py` and
   `app/application/script_composer.py` callers.
"""
from __future__ import annotations

import logging
import uuid
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError, NotFoundError
from app.models.memory_entry import MemoryEntry
from app.models.merchant import Merchant
from app.models.offer import Offer

logger = logging.getLogger(__name__)


# Hard cap on active entries per (scope_type, scope_id). Crossing this
# is a sign of preference sprawl; we'd rather refuse the write and
# surface a curate prompt than silently keep growing the suffix block
# the model has to read on every generation.
_MAX_ACTIVE_PER_SCOPE = 50
_ALLOWED_SURFACES = {"all", "image", "script"}


# ── CRUD ────────────────────────────────────────────────────────────


async def add_memory(
    db: AsyncSession,
    *,
    scope_type: str,
    scope_id: uuid.UUID,
    content: str,
    surface: str = "all",
    source: str = "manual",
    source_ref: str | None = None,
) -> MemoryEntry:
    """Persist a new memory.

    Resolves merchant_id from the scope (offer.merchant_id when
    scope_type='offer'; scope_id itself when scope_type='merchant')
    so callers don't have to pre-fetch. Raises NotFoundError if the
    scope target doesn't exist — clearer than a generic FK violation.
    """
    _validate_surface(surface)
    merchant_id = await _resolve_merchant_id(db, scope_type, scope_id)

    # Cap check — count active rows for this exact scope.
    active_count = await _count_active(db, scope_type, scope_id)
    if active_count >= _MAX_ACTIVE_PER_SCOPE:
        raise AppError(
            "MEMORY_CAP_REACHED",
            f"Reached {_MAX_ACTIVE_PER_SCOPE} active memories for this "
            f"{scope_type}. Remove or deactivate older entries first.",
            400,
        )

    entry = MemoryEntry(
        merchant_id=merchant_id,
        scope_type=scope_type,
        scope_id=scope_id,
        content=content.strip(),
        surface=surface,
        source=source,
        source_ref=source_ref,
        is_active=True,
    )
    db.add(entry)
    await db.flush()
    await db.commit()
    await db.refresh(entry)
    return entry


async def update_memory(
    db: AsyncSession,
    memory_id: uuid.UUID,
    *,
    content: str | None = None,
    surface: str | None = None,
    is_active: bool | None = None,
) -> MemoryEntry:
    entry = await db.get(MemoryEntry, memory_id)
    if entry is None:
        raise NotFoundError("MemoryEntry", str(memory_id))

    if content is not None:
        entry.content = content.strip()
    if surface is not None:
        _validate_surface(surface)
        entry.surface = surface
    if is_active is not None:
        entry.is_active = is_active

    await db.commit()
    await db.refresh(entry)
    return entry


async def delete_memory(db: AsyncSession, memory_id: uuid.UUID) -> None:
    """Hard delete. The UI's "stop using" toggle calls
    update_memory(is_active=False); DELETE is for "I never want to see
    this row again" (typos, wrong scope, etc.)."""
    entry = await db.get(MemoryEntry, memory_id)
    if entry is None:
        raise NotFoundError("MemoryEntry", str(memory_id))
    await db.delete(entry)
    await db.commit()


# ── Retrieval ────────────────────────────────────────────────────────


async def list_memories_for_offer(
    db: AsyncSession,
    *,
    offer_id: uuid.UUID,
    surface: str | None = None,
    active_only: bool = True,
) -> list[MemoryEntry]:
    """The hot-path retrieval used by every prompt assembler.

    Returns offer-scoped + merchant-scoped memories combined. Sort:
    offer-scoped before merchant-scoped (more specific first); within
    each tier, newest first (most recent feedback wins highest weight).

    `surface` filter, when set, returns entries whose surface matches
    the requested value OR equals 'all'. So a script assembler asking
    surface='script' gets all script-targeted memories AND every
    cross-cutting memory.
    """
    offer = await db.get(Offer, offer_id)
    if offer is None:
        return []

    stmt = select(MemoryEntry).where(
        MemoryEntry.merchant_id == offer.merchant_id,
        (
            (
                (MemoryEntry.scope_type == "offer")
                & (MemoryEntry.scope_id == offer_id)
            )
            | (
                (MemoryEntry.scope_type == "merchant")
                & (MemoryEntry.scope_id == offer.merchant_id)
            )
        ),
    )
    if active_only:
        stmt = stmt.where(MemoryEntry.is_active.is_(True))
    if surface:
        _validate_surface(surface)
        stmt = stmt.where(MemoryEntry.surface.in_([surface, "all"]))

    rows = (await db.execute(stmt)).scalars().all()
    # Sort in Python — small lists (≤100), and the two-tier ordering
    # is easier to read than a SQL CASE expression.
    return sorted(
        rows,
        key=lambda m: (
            0 if m.scope_type == "offer" else 1,
            -m.created_at.timestamp(),
        ),
    )


async def list_memories_by_scope(
    db: AsyncSession,
    *,
    merchant_id: uuid.UUID,
    scope_type: str | None = None,
    scope_id: uuid.UUID | None = None,
    surface: str | None = None,
    active_only: bool = True,
) -> list[MemoryEntry]:
    """List API entry — flexible filters for the management UI.

    Different from ``list_memories_for_offer``: this returns ONLY rows
    matching the literal (scope_type, scope_id) pair, no merge. Used
    by GET /memories with explicit query params.
    """
    stmt = select(MemoryEntry).where(MemoryEntry.merchant_id == merchant_id)
    if scope_type is not None:
        stmt = stmt.where(MemoryEntry.scope_type == scope_type)
    if scope_id is not None:
        stmt = stmt.where(MemoryEntry.scope_id == scope_id)
    if surface is not None:
        _validate_surface(surface)
        stmt = stmt.where(MemoryEntry.surface.in_([surface, "all"]))
    if active_only:
        stmt = stmt.where(MemoryEntry.is_active.is_(True))

    rows = (await db.execute(stmt)).scalars().all()
    return sorted(rows, key=lambda m: -m.created_at.timestamp())


# ── Prompt rendering ────────────────────────────────────────────────


def render_memories_block(
    memories: Iterable[MemoryEntry], lang: str = "zh"
) -> str:
    """Format memories as a prompt suffix. Empty list → empty string
    (caller can `prompt += render_memories_block(...)` unconditionally).

    Why suffix vs. prefix:
      * Prefix would mix with the brief and risk being treated as part
        of the scene description.
      * Suffix is positioned as the LAST instruction the model reads,
        which models weight more heavily for constraint compliance —
        and the wording explicitly says "with conflicts above, this
        section wins".

    Why language-matched header (not UI-locale): the rest of the
    prompt is in the article / brief language; flipping just the
    header to UI locale would visually break the prompt for the model.
    """
    items = [m for m in memories if (m.content or "").strip()]
    if not items:
        return ""

    if lang.startswith("en"):
        header = (
            "User preferences (must strictly follow; "
            "where this conflicts with rules above, this section wins):"
        )
    else:
        header = (
            "用户偏好（必须严格遵守；"
            "与上文规则冲突时以本节为准）："
        )

    lines = [header]
    for i, m in enumerate(items, start=1):
        lines.append(f"{i}. {m.content.strip()}")

    return "\n\n---\n" + "\n".join(lines) + "\n---"


# ── Internal helpers ────────────────────────────────────────────────


async def _resolve_merchant_id(
    db: AsyncSession, scope_type: str, scope_id: uuid.UUID
) -> uuid.UUID:
    if scope_type == "merchant":
        merchant = await db.get(Merchant, scope_id)
        if merchant is None:
            raise NotFoundError("Merchant", str(scope_id))
        return merchant.id
    if scope_type == "offer":
        offer = await db.get(Offer, scope_id)
        if offer is None:
            raise NotFoundError("Offer", str(scope_id))
        return offer.merchant_id
    raise AppError(
        "INVALID_SCOPE_TYPE",
        f"scope_type must be 'merchant' or 'offer', got {scope_type!r}",
        400,
    )


def _validate_surface(surface: str) -> None:
    if surface not in _ALLOWED_SURFACES:
        raise AppError(
            "INVALID_MEMORY_SURFACE",
            "surface must be one of: all, image, script",
            400,
        )


async def _count_active(
    db: AsyncSession, scope_type: str, scope_id: uuid.UUID
) -> int:
    stmt = select(func.count(MemoryEntry.id)).where(
        MemoryEntry.scope_type == scope_type,
        MemoryEntry.scope_id == scope_id,
        MemoryEntry.is_active.is_(True),
    )
    return int((await db.execute(stmt)).scalar_one() or 0)
