import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PaginationDep
from app.application.strategy_unit_service import StrategyUnitService
from app.database import get_db
from app.schemas.common import PaginatedResponse
from app.schemas.strategy_unit import StrategyUnitCreate, StrategyUnitResponse, StrategyUnitUpdate

router = APIRouter(prefix="/strategy-units", tags=["strategy-units"])


@router.post("", response_model=StrategyUnitResponse, status_code=201)
async def create_strategy_unit(data: StrategyUnitCreate, db: AsyncSession = Depends(get_db)):
    svc = StrategyUnitService(db)
    return await svc.create(data)


@router.get("", response_model=PaginatedResponse[StrategyUnitResponse])
async def list_strategy_units(
    pagination: PaginationDep,
    offer_id: uuid.UUID | None = Query(None),
    merchant_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    svc = StrategyUnitService(db)
    items, total = await svc.list(offer_id=offer_id, merchant_id=merchant_id, **pagination)
    return PaginatedResponse(items=items, total=total, **pagination)


@router.get("/{unit_id}", response_model=StrategyUnitResponse)
async def get_strategy_unit(unit_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = StrategyUnitService(db)
    return await svc.get(unit_id)


@router.patch("/{unit_id}", response_model=StrategyUnitResponse)
async def update_strategy_unit(
    unit_id: uuid.UUID, data: StrategyUnitUpdate, db: AsyncSession = Depends(get_db)
):
    svc = StrategyUnitService(db)
    return await svc.update(unit_id, data)


@router.delete("/{unit_id}", status_code=204)
async def delete_strategy_unit(unit_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = StrategyUnitService(db)
    await svc.delete(unit_id)
