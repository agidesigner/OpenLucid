import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PaginationDep
from app.application.topic_plan_service import TopicPlanService
from app.database import get_db
from app.schemas.common import PaginatedResponse
from app.schemas.topic_plan import TopicPlanResponse

router = APIRouter(prefix="/topic-plans", tags=["topic-plans"])


@router.get("", response_model=PaginatedResponse[TopicPlanResponse])
async def list_topic_plans(
    pagination: PaginationDep,
    offer_id: uuid.UUID | None = Query(None),
    strategy_unit_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    svc = TopicPlanService(db)
    items, total = await svc.list(offer_id=offer_id, strategy_unit_id=strategy_unit_id, **pagination)
    return PaginatedResponse(items=items, total=total, **pagination)


@router.get("/{topic_id}", response_model=TopicPlanResponse)
async def get_topic_plan(topic_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    svc = TopicPlanService(db)
    return await svc.get(topic_id)
