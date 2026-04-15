import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PaginationDep
from app.application.creation_service import CreationService
from app.database import get_db
from app.schemas.common import PaginatedResponse
from app.schemas.creation import CreationCreate, CreationResponse, CreationUpdate

router = APIRouter(prefix="/creations", tags=["creations"])


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
