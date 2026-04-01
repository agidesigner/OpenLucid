from __future__ import annotations

import uuid

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.topic_plan import TopicPlan


class TopicPlanRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> TopicPlan:
        plan = TopicPlan(**kwargs)
        self.session.add(plan)
        await self.session.flush()
        return plan

    async def get_by_id(self, plan_id: uuid.UUID) -> TopicPlan | None:
        return await self.session.get(TopicPlan, plan_id)

    async def list(
        self,
        offer_id: uuid.UUID | None = None,
        strategy_unit_id: uuid.UUID | None = None,
        language: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[TopicPlan], int]:
        base = select(TopicPlan)
        count_base = select(func.count()).select_from(TopicPlan)

        if offer_id:
            base = base.where(TopicPlan.offer_id == offer_id)
            count_base = count_base.where(TopicPlan.offer_id == offer_id)
        if strategy_unit_id:
            base = base.where(TopicPlan.strategy_unit_id == strategy_unit_id)
            count_base = count_base.where(TopicPlan.strategy_unit_id == strategy_unit_id)
        if language:
            base = base.where(TopicPlan.language == language)
            count_base = count_base.where(TopicPlan.language == language)

        total = (await self.session.execute(count_base)).scalar_one()
        stmt = base.offset(offset).limit(limit).order_by(TopicPlan.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def count_by_offer(self, offer_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(TopicPlan).where(TopicPlan.offer_id == offer_id)
        return (await self.session.execute(stmt)).scalar_one()

    async def count_by_strategy_unit(self, unit_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(TopicPlan).where(TopicPlan.strategy_unit_id == unit_id)
        return (await self.session.execute(stmt)).scalar_one()

    async def update_rating(self, plan_id: uuid.UUID, rating: int | None) -> TopicPlan | None:
        plan = await self.get_by_id(plan_id)
        if not plan:
            return None
        plan.user_rating = rating
        await self.session.flush()
        return plan

    async def list_rated(
        self,
        offer_id: uuid.UUID,
        rating: int,
        limit: int = 30,
    ) -> list[TopicPlan]:
        """List topics with a specific rating (1=liked, -1=disliked) for an offer."""
        stmt = (
            select(TopicPlan)
            .where(TopicPlan.offer_id == offer_id, TopicPlan.user_rating == rating)
            .order_by(TopicPlan.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
