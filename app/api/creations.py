import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PaginationDep
from app.application.creation_service import CreationService
from app.database import get_db
from app.models.creation import Creation
from app.models.offer import Offer
from app.schemas.common import PaginatedResponse
from app.schemas.creation import CreationCreate, CreationResponse, CreationUpdate

router = APIRouter(prefix="/creations", tags=["creations"])


@router.get("/offer-counts")
async def get_offer_counts(
    merchant_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Aggregate creation counts per offer, joined with offer names so
    the creations.html offer-filter dropdown can render
    ``<offer name> (<count>)`` directly.

    Returns ``[{offer_id, name, count}]`` sorted by count descending.
    Same self-pruning pattern as /source-counts: offers with zero
    creations don't appear.
    """
    stmt = (
        select(Creation.offer_id, Offer.name, func.count(Creation.id).label("c"))
        .join(Offer, Offer.id == Creation.offer_id)
    )
    if merchant_id:
        stmt = stmt.where(Creation.merchant_id == merchant_id)
    stmt = stmt.group_by(Creation.offer_id, Offer.name).order_by(func.count(Creation.id).desc())
    rows = (await db.execute(stmt)).all()
    return [
        {"offer_id": str(oid), "name": name, "count": int(c)}
        for oid, name, c in rows
    ]


@router.get("/source-counts", response_model=dict[str, int])
async def get_source_counts(
    merchant_id: uuid.UUID | None = Query(None),
    offer_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Aggregate creation counts by ``source_app``.

    Used by creations.html to prune the source-filter dropdown — without
    this, the dropdown padded every theoretically-possible source (incl.
    apps that the user has never used), most of them silent dead options
    that returned 0 results when picked.

    Filters mirror the listing endpoint's optional scope so a per-offer
    or per-merchant view returns counts for only that subset.
    """
    stmt = select(Creation.source_app, func.count(Creation.id))
    if merchant_id:
        stmt = stmt.where(Creation.merchant_id == merchant_id)
    if offer_id:
        stmt = stmt.where(Creation.offer_id == offer_id)
    stmt = stmt.group_by(Creation.source_app)

    rows = (await db.execute(stmt)).all()
    return {sa: int(c) for sa, c in rows if sa}


@router.get("", response_model=PaginatedResponse[CreationResponse])
async def list_creations(
    pagination: PaginationDep,
    merchant_id: uuid.UUID | None = Query(None),
    offer_id: uuid.UUID | None = Query(None),
    content_type: str | None = Query(None),
    source_app: str | None = Query(None),
    q: str | None = Query(None, description="Search in title and content"),
    db: AsyncSession = Depends(get_db),
):
    svc = CreationService(db)
    items, total = await svc.list(
        merchant_id=merchant_id,
        offer_id=offer_id,
        content_type=content_type,
        source_app=source_app,
        q=q,
        **pagination,
    )
    # Enrich with video summary so the list UI can show video badges inline.
    summaries = await svc.get_video_summaries([c.id for c in items])
    for c in items:
        s = summaries.get(c.id)
        c.video_count = s["count"] if s else 0
        c.latest_video = s["latest"] if s else None
    return PaginatedResponse(items=items, total=total, **pagination)


@router.get("/{creation_id}", response_model=CreationResponse)
async def get_creation(creation_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = CreationService(db)
    return await svc.get(creation_id)


@router.post("", response_model=CreationResponse, status_code=201)
async def create_creation(data: CreationCreate, db: AsyncSession = Depends(get_db)):
    svc = CreationService(db)
    creation = await svc.create(data)
    await db.commit()
    await db.refresh(creation)
    return creation


@router.patch("/{creation_id}", response_model=CreationResponse)
async def update_creation(
    creation_id: uuid.UUID,
    data: CreationUpdate,
    db: AsyncSession = Depends(get_db),
):
    svc = CreationService(db)
    creation = await svc.update(creation_id, data)
    await db.commit()
    await db.refresh(creation)
    return creation


@router.delete("/{creation_id}", status_code=204)
async def delete_creation(creation_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = CreationService(db)
    await svc.delete(creation_id)
    await db.commit()


@router.post("/{creation_id}/regenerate-broll-plan")
async def regenerate_broll_plan(
    creation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Ask the LLM for a fresh ``broll_plan`` while keeping the script text
    untouched. Persists the new plan onto ``creation.structured_content`` so
    the Generate Video modal (which deep-copies from there) picks it up.
    """
    svc = CreationService(db)
    creation = await svc.regenerate_broll_plan(creation_id)
    await db.commit()
    await db.refresh(creation)
    return {
        "broll_plan": (creation.structured_content or {}).get("broll_plan", []),
    }
