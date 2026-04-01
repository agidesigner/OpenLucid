import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PaginationDep
from app.application.strategy_unit_link_service import (
    StrategyUnitAssetLinkService,
    StrategyUnitKnowledgeLinkService,
)
from app.database import get_db
from app.schemas.common import PaginatedResponse
from app.schemas.strategy_unit_link import (
    AssetLinkCreate,
    AssetLinkResponse,
    KnowledgeLinkCreate,
    KnowledgeLinkResponse,
)

router = APIRouter(prefix="/strategy-units/{unit_id}", tags=["strategy-unit-links"])


# ── Knowledge Links ──────────────────────────────────────────────


@router.post("/knowledge-links", response_model=KnowledgeLinkResponse, status_code=201)
async def create_knowledge_link(
    unit_id: uuid.UUID, data: KnowledgeLinkCreate, db: AsyncSession = Depends(get_db)
):
    svc = StrategyUnitKnowledgeLinkService(db)
    return await svc.create(unit_id, data)


@router.get("/knowledge-links", response_model=PaginatedResponse[KnowledgeLinkResponse])
async def list_knowledge_links(
    unit_id: uuid.UUID, pagination: PaginationDep, db: AsyncSession = Depends(get_db)
):
    svc = StrategyUnitKnowledgeLinkService(db)
    items, total = await svc.list(unit_id, **pagination)
    return PaginatedResponse(items=items, total=total, **pagination)


@router.delete("/knowledge-links/{link_id}", status_code=204)
async def delete_knowledge_link(
    unit_id: uuid.UUID, link_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    svc = StrategyUnitKnowledgeLinkService(db)
    await svc.delete(unit_id, link_id)


# ── Asset Links ──────────────────────────────────────────────────


@router.post("/asset-links", response_model=AssetLinkResponse, status_code=201)
async def create_asset_link(
    unit_id: uuid.UUID, data: AssetLinkCreate, db: AsyncSession = Depends(get_db)
):
    svc = StrategyUnitAssetLinkService(db)
    return await svc.create(unit_id, data)


@router.get("/asset-links", response_model=PaginatedResponse[AssetLinkResponse])
async def list_asset_links(
    unit_id: uuid.UUID, pagination: PaginationDep, db: AsyncSession = Depends(get_db)
):
    svc = StrategyUnitAssetLinkService(db)
    items, total = await svc.list(unit_id, **pagination)
    return PaginatedResponse(items=items, total=total, **pagination)


@router.delete("/asset-links/{link_id}", status_code=204)
async def delete_asset_link(
    unit_id: uuid.UUID, link_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    svc = StrategyUnitAssetLinkService(db)
    await svc.delete(unit_id, link_id)
