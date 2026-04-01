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
