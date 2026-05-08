"""Memory CRUD API.

Surface is intentionally thin (POST/GET/PATCH/DELETE on a flat resource)
because memory_service does the heavy lifting (scope merge, cap, audit).
The route layer's job is only:
  - Pydantic validation in / out
  - resolve query params → service args
  - map service exceptions → HTTP codes (handled centrally in app)

Two GET shapes:
  * GET /api/v1/memories?scope_type=offer&scope_id=... — exact-scope
    list, used by the offer.html management tab. Returns only the rows
    matching that literal (scope_type, scope_id) pair.
  * GET /api/v1/memories/for-offer/{offer_id} — merged view for the
    SAME offer + its merchant. Used when a UI needs to show "everything
    that applies to this offer right now". Mirrors the prompt-assembly
    contract.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.application.memory_service import (
    add_memory,
    delete_memory,
    list_memories_by_scope,
    list_memories_for_offer,
    update_memory,
)
from app.exceptions import NotFoundError
from app.models.merchant import Merchant
from app.models.offer import Offer
from app.schemas.memory import (
    MemoryCreate,
    MemoryListResponse,
    MemoryResponse,
    MemoryUpdate,
)

router = APIRouter(prefix="/memories", tags=["memory"])


@router.post("", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED)
async def create_memory(
    data: MemoryCreate,
    db: AsyncSession = Depends(get_db),
):
    entry = await add_memory(
        db,
        scope_type=data.scope_type,
        scope_id=data.scope_id,
        content=data.content,
        surface=data.surface,
        source=data.source,
        source_ref=data.source_ref,
    )
    return MemoryResponse.model_validate(entry)


@router.get("", response_model=MemoryListResponse)
async def list_memories(
    scope_type: str | None = Query(None, pattern="^(merchant|offer)$"),
    scope_id: uuid.UUID | None = Query(None),
    surface: str | None = Query(None, pattern="^(all|image|script)$"),
    active_only: bool = Query(True),
    db: AsyncSession = Depends(get_db),
):
    """Exact-scope listing for the management UI.

    Caller must supply either both scope_type and scope_id, or neither
    (in which case all memories under the resolved merchant are
    returned). The frontend tab on /offer.html?id=X always passes
    both, narrowed to that offer.

    The merchant_id is derived from scope_id (the offer's merchant or
    the merchant itself) so the caller doesn't have to know it. When
    no scope is given, we fall back to the only merchant in a
    single-tenant install.
    """
    merchant_id = await _resolve_merchant_id_for_query(db, scope_type, scope_id)
    items = await list_memories_by_scope(
        db,
        merchant_id=merchant_id,
        scope_type=scope_type,
        scope_id=scope_id,
        surface=surface,
        active_only=active_only,
    )
    return MemoryListResponse(items=[MemoryResponse.model_validate(m) for m in items])


@router.get("/for-offer/{offer_id}", response_model=MemoryListResponse)
async def list_for_offer(
    offer_id: uuid.UUID,
    surface: str | None = Query(None, pattern="^(all|image|script)$"),
    active_only: bool = Query(True),
    db: AsyncSession = Depends(get_db),
):
    """Merged view: offer-scoped + merchant-scoped, in prompt order
    (most-specific first, newest first within each tier). Mirrors the
    contract every prompt assembler uses internally."""
    items = await list_memories_for_offer(
        db,
        offer_id=offer_id,
        surface=surface,
        active_only=active_only,
    )
    return MemoryListResponse(items=[MemoryResponse.model_validate(m) for m in items])


@router.patch("/{memory_id}", response_model=MemoryResponse)
async def patch(
    memory_id: uuid.UUID,
    data: MemoryUpdate,
    db: AsyncSession = Depends(get_db),
):
    entry = await update_memory(
        db,
        memory_id,
        content=data.content,
        surface=data.surface,
        is_active=data.is_active,
    )
    return MemoryResponse.model_validate(entry)


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove(memory_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    await delete_memory(db, memory_id)


# ── helper ──


async def _resolve_merchant_id_for_query(
    db: AsyncSession,
    scope_type: str | None,
    scope_id: uuid.UUID | None,
) -> uuid.UUID:
    """When the caller passed (scope_type, scope_id), derive merchant
    from those. Otherwise fall back to the single configured merchant
    (we're a single-user product — multiple merchants exist as a
    feature but the typical install has one)."""
    if scope_type == "merchant" and scope_id is not None:
        return scope_id
    if scope_type == "offer" and scope_id is not None:
        offer = await db.get(Offer, scope_id)
        if offer is None:
            raise NotFoundError("Offer", str(scope_id))
        return offer.merchant_id

    # No scope filter — pick the first merchant. In single-tenant
    # installs this is unambiguous; in multi-merchant installs the
    # frontend always sends a scope, so this branch is rarely hit.
    from sqlalchemy import select
    merchant = (await db.execute(select(Merchant).limit(1))).scalar_one_or_none()
    if merchant is None:
        raise NotFoundError("Merchant", "no merchant configured")
    return merchant.id
