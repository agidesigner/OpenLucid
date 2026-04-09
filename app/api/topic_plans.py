import uuid

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PaginationDep
from app.application.topic_plan_service import TopicPlanService
from app.database import get_db
from app.schemas.common import PaginatedResponse
from app.schemas.topic_plan import TopicPlanResponse

router = APIRouter(prefix="/topic-plans", tags=["topic-plans"])


class RatingRequest(BaseModel):
    rating: int | None = Field(None, ge=-1, le=1)  # 1=like, -1=dislike, None=clear


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


@router.patch("/{topic_id}/rating", response_model=TopicPlanResponse)
async def rate_topic_plan(
    topic_id: uuid.UUID,
    body: RatingRequest,
    db: AsyncSession = Depends(get_db),
):
    from app.application.creation_service import CreationService
    from app.infrastructure.topic_plan_repo import TopicPlanRepository
    from app.schemas.creation import CreationCreate

    repo = TopicPlanRepository(db)
    plan = await repo.update_rating(topic_id, body.rating)
    if not plan:
        raise HTTPException(status_code=404, detail="Topic plan not found")

    # P1: when user 👍 a topic, also save it to creations as a "kept idea".
    # Idempotent: skip if already saved (look up by source_note containing topic id).
    if body.rating == 1:
        try:
            from sqlalchemy import select
            from app.models.creation import Creation

            existing = await db.execute(
                select(Creation.id)
                .where(Creation.source_app == "topic_studio")
                .where(Creation.source_note == f"topic_plan:{topic_id}")
                .limit(1)
            )
            if not existing.first():
                # Build content from title + hook + key_points
                parts = [plan.title]
                if plan.hook:
                    parts.append(f"\nHook: {plan.hook}")
                if plan.key_points_json:
                    kp_list = plan.key_points_json if isinstance(plan.key_points_json, list) else []
                    if kp_list:
                        parts.append("\nKey points:\n" + "\n".join(f"- {p}" for p in kp_list))
                content = "\n".join(parts)

                svc = CreationService(db)
                tags = []
                if plan.angle:
                    tags.append(plan.angle)
                if plan.channel:
                    tags.append(plan.channel)
                await svc.create(CreationCreate(
                    title=plan.title[:512],
                    content=content,
                    content_type="topic",
                    merchant_id=plan.merchant_id,
                    offer_id=plan.offer_id,
                    tags=tags or None,
                    source_app="topic_studio",
                    source_note=f"topic_plan:{topic_id}",
                ))
        except Exception as e:
            # Auto-save is best-effort: never break the rating action
            import logging
            logging.getLogger(__name__).warning(
                "Auto-save creation from topic %s failed: %s", topic_id, e
            )

    await db.commit()
    await db.refresh(plan)
    return plan
