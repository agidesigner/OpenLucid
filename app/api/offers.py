import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PaginationDep
from app.application.context_service import ContextService
from app.application.offer_service import OfferService
from app.database import get_db
from app.schemas.common import PaginatedResponse
from app.schemas.context import OfferContextSummary
from app.schemas.offer import OfferCreate, OfferResponse, OfferUpdate

router = APIRouter(prefix="/offers", tags=["offers"])


@router.post("", response_model=OfferResponse, status_code=201)
async def create_offer(data: OfferCreate, db: AsyncSession = Depends(get_db)):
    svc = OfferService(db)
    return await svc.create(data)


@router.get("", response_model=PaginatedResponse[OfferResponse])
async def list_offers(
    pagination: PaginationDep,
    merchant_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    svc = OfferService(db)
    items, total = await svc.list(merchant_id=merchant_id, **pagination)
    return PaginatedResponse(items=items, total=total, **pagination)


@router.get("/{offer_id}", response_model=OfferResponse)
async def get_offer(offer_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = OfferService(db)
    return await svc.get(offer_id)


@router.get("/{offer_id}/context", response_model=OfferContextSummary)
async def get_offer_context(offer_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = ContextService(db)
    return await svc.get_offer_context(offer_id)


@router.patch("/{offer_id}", response_model=OfferResponse)
async def update_offer(
    offer_id: uuid.UUID, data: OfferUpdate, db: AsyncSession = Depends(get_db)
):
    svc = OfferService(db)
    return await svc.update(offer_id, data)


@router.delete("/{offer_id}", status_code=204)
async def delete_offer(offer_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = OfferService(db)
    await svc.delete(offer_id)
