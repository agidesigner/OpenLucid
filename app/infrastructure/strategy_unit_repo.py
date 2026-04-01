import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.strategy_unit import StrategyUnit


class StrategyUnitRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> StrategyUnit:
        unit = StrategyUnit(**kwargs)
        self.session.add(unit)
        await self.session.flush()
        return unit

    async def get_by_id(self, unit_id: uuid.UUID) -> StrategyUnit | None:
        return await self.session.get(StrategyUnit, unit_id)

    async def list(
        self,
        offer_id: uuid.UUID | None = None,
        merchant_id: uuid.UUID | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[StrategyUnit], int]:
        base = select(StrategyUnit)
        count_base = select(func.count()).select_from(StrategyUnit)

        if offer_id:
            base = base.where(StrategyUnit.offer_id == offer_id)
            count_base = count_base.where(StrategyUnit.offer_id == offer_id)
        if merchant_id:
            base = base.where(StrategyUnit.merchant_id == merchant_id)
            count_base = count_base.where(StrategyUnit.merchant_id == merchant_id)

        total = (await self.session.execute(count_base)).scalar_one()
        stmt = base.offset(offset).limit(limit).order_by(StrategyUnit.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def count_by_offer(self, offer_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(StrategyUnit).where(StrategyUnit.offer_id == offer_id)
        return (await self.session.execute(stmt)).scalar_one()

    async def delete(self, unit: StrategyUnit) -> None:
        await self.session.delete(unit)
        await self.session.flush()

    async def update(self, unit: StrategyUnit, **kwargs) -> StrategyUnit:
        for key, value in kwargs.items():
            if value is not None:
                setattr(unit, key, value)
        await self.session.flush()
        await self.session.refresh(unit)
        return unit
